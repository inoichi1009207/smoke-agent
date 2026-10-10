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
from . import session_probe as sp

ITEM = "session"
PARTS = ("signout_isolation", "inactive_rejected", "revoke")
FINDING, CLEAN, NOT_RUN = "finding", "clean", "not run"

SIGNOUT_ALL = re.compile(
    r"(?i)(?<!not )\bsign(?:ing|s)?[- ]?out\b[^.]{0,60}?(?<!not )(?<!n't )\b(?:ends|terminates|invalidates|revokes|"
    r"signs\s+out|logs\s+out|closes)\b[^.]{0,30}?\b(?:all|every)\b[^.]{0,30}?\b(?:sessions?|devices?|browsers?)\b|"
    r"\bsign(?:s|ing)?[- ]?out\s+(?:everywhere|of\s+(?:all|every)\s+(?:sessions?|devices?|browsers?))\b[^.]{0,40}?"
    r"\b(?:when|after|on)\b[^.]{0,20}\bsign(?:s|ing)?[- ]?out\b")

SERVER_END = re.compile(
    r"(?i)\brevok|\binvalidat|\b(?:sign|log)(?:s|ged)?[- ]?out\s+(?:of\s+)?(?:other|every|all)\b|"
    r"\bend(?:s|ed)?\s+(?:the\s+)?other\s+sessions?\b|\bcannot\s+restore\b[^.]{0,40}\bsession\b|"
    r"\bsession\s+state\b")
REVOKE_WORD = re.compile(r"(?i)^(?:revoke|revocation|end|terminate|deactivate|invalidate|kill|expire|sign-?out|log-?out|"
                         r"logoff)$")
OTHERS_WORD = re.compile(r"(?i)(?:^|[-_])(?:others?|all|everywhere|rest)(?:$|[-_])")
SESSION_SEG = re.compile(r"(?i)sessions?")
END_WORD = re.compile(r"(?i)(?:^|[-_/])(?:revoke|terminate|invalidate|deactivate|sign-?out|log-?out|logoff|end)(?:$|[-_/])")
ID_KEY = re.compile(r"(?i)id$|session")
ID_KEYS = ("id", "sessionId", "session_id", "sid", "uuid", "_id", "key")
CURRENT_KEYS = ("current", "is_current", "isCurrent", "currentSession", "current_session", "this_device", "thisDevice",
                "is_this_device", "self")
TOKEN_KEYS = ("token", "accessToken", "access_token", "sessionToken", "session_token", "jwt", "authToken", "auth_token")
BODY_LIMIT = 200_000

class _OutOfTime(Exception):
    pass

class _Jar:

    def __init__(self, name: str) -> None:
        self.name, self.cookies, self.bearer = name, {}, None

    def copy(self, name: str) -> "_Jar":
        other = _Jar(name)
        other.cookies, other.bearer = dict(self.cookies), self.bearer
        return other

    def empty(self) -> bool:
        return not self.cookies and not self.bearer

class _Resp:
    def __init__(self, status: int, text: str, set_cookies: dict[str, str], cleared: set[str]) -> None:
        self.status, self.text, self.set_cookies, self.cleared = status, text, set_cookies, cleared

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except ValueError:
            return None

def _http(base: str, method: str, path: str, body: dict | None, jar: _Jar | None, deadline: float) -> _Resp:
    left = deadline - time.time()
    if left < 1.0:
        raise _OutOfTime()
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method.upper())
    if data is not None:
        req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    if jar is not None and jar.cookies:
        req.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in jar.cookies.items()))
    if jar is not None and jar.bearer:
        req.add_header("Authorization", "Bearer " + jar.bearer)
    opener = urllib.request.build_opener(sp._NoRedirect)
    headers = None
    try:
        resp = opener.open(req, timeout=max(1.0, min(10.0, left)))
        status, headers, raw = resp.status, resp.headers, resp.read(BODY_LIMIT)
    except urllib.error.HTTPError as exc:
        status, headers = exc.code, exc.headers
        try:
            raw = exc.read(BODY_LIMIT)
        except Exception:
            raw = b""
    except (urllib.error.URLError, OSError, ValueError):
        return _Resp(0, "", {}, set())
    got: dict[str, str] = {}
    cleared: set[str] = set()
    for line in (headers.get_all("Set-Cookie") or []) if headers else []:
        m = re.match(r"\s*([^=;\s]+)=([^;]*)", line)
        if not m:
            continue
        if not m.group(2) or re.search(r"(?i)max-age=0\b|max-age=-|expires=Thu, 01 Jan 1970", line):
            cleared.add(m.group(1))
        else:
            got[m.group(1)] = m.group(2)
    return _Resp(status, raw.decode("utf-8", "replace"), got, cleared)

def _apply(jar: _Jar, resp: _Resp) -> None:
    for name in resp.cleared:
        jar.cookies.pop(name, None)
    jar.cookies.update(resp.set_cookies)

def _token(obj: Any, depth: int = 0) -> str | None:
    if isinstance(obj, dict) and depth <= 2:
        for key in TOKEN_KEYS:
            value = obj.get(key)
            if isinstance(value, str) and len(value) >= 16:
                return value
        for value in obj.values():
            found = _token(value, depth + 1)
            if found:
                return found
    return None

def _ok(status: int) -> bool:
    return 200 <= status < 300

def _front_text(project_dir: Path) -> str:
    root = Path(project_dir) / "frontend" / "src"
    parts: list[str] = []
    size = 0
    if root.is_dir():
        for folder, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d not in js.SKIP_DIRS]
            for name in names:
                if name.endswith((".ts", ".tsx", ".js", ".jsx")) and size < 3_000_000:
                    try:
                        text = Path(folder, name).read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    parts.append(text)
                    size += len(text)
    return "\n".join(parts)

def _named(path: str, local: str, front: str) -> bool:
    return any(q + local in front for q in ("'", '"', "`")) or path in front

def session_routes(project_dir: Path) -> dict[str, list[dict[str, Any]]]:
    files = js.backend_files(project_dir)
    prefixes = js.mount_prefixes(files)
    front = _front_text(project_dir)
    out: dict[str, list[dict[str, Any]]] = {"logout": [], "list": [], "revoke": []}
    seen: set[tuple[str, str]] = set()
    for f in files:
        for route in f.routes:
            segs = [s for s in route["path"].split("/") if s]
            method = route["method"]
            if not segs or method not in ("get", "post", "delete", "put", "patch"):
                continue
            bases = prefixes.get(f.rel)
            if bases is None:
                continue
            last = segs[-1]
            params = [i for i, s in enumerate(segs) if s.startswith(":") or s.startswith("{")]
            sess = [i for i, s in enumerate(segs) if SESSION_SEG.search(s) and not s.startswith(":")]
            kind = None
            if sp.LOGOUT_SEG.match(last) and len(segs) <= 3:
                kind = "logout"
            elif method == "delete" and not params and re.fullmatch(r"(?i)session|current(?:-session)?", last):
                kind = "logout"
            elif method == "get":
                if sess and not params and re.search(r"(?i)sessions", segs[sess[-1]]) and sess[-1] == len(segs) - 1:
                    kind = "list"
            elif OTHERS_WORD.search(last) and (END_WORD.search(last) or (sess and END_WORD.search("/".join(segs))) or
                                                 (sess and method == "delete")) and not params:
                kind = "others"
            elif sess and len(params) == 1 and params[0] > sess[0] and not OTHERS_WORD.search(last):
                if (method == "delete" and params[0] == len(segs) - 1) or \
                        (params[0] == len(segs) - 2 and REVOKE_WORD.match(last) and method in ("post", "put", "patch", "delete")):
                    kind = "id"
            elif sess and not params and REVOKE_WORD.match(last) and method != "get" and                     any(ID_KEY.search(k) for k in sp.body_keys(sp._handler_text(f, route))):
                kind = "body"
            if kind is None:
                continue
            for base in bases:
                path = js.join_path(base, route["path"])
                if (method, path) in seen:
                    continue
                seen.add((method, path))
                entry: dict[str, Any] = {"path": path, "method": method, "where": f"{f.rel}:{route['line']}",
                                         "named": _named(path, route["path"], front)}
                if kind in ("logout", "list"):
                    out[kind].append(entry)
                else:
                    entry["kind"] = kind
                    entry["keys"] = sp.body_keys(sp._handler_text(f, route))
                    out["revoke"].append(entry)
    order = {"id": 0, "body": 1, "others": 2}
    out["logout"].sort(key=lambda r: (not r["named"], len(r["path"])))
    out["list"].sort(key=lambda r: (not r["named"], len(r["path"])))
    out["revoke"].sort(key=lambda r: (order[r["kind"]], not r["named"], r["method"] != "delete", len(r["path"])))
    return out

def session_chain_probe(requirement: str, project_dir: Path, *, time_limit: float = 180.0, log=print,
                        node_modules: str | None = None) -> dict:
    deadline = time.time() + max(0.0, float(time_limit or 0))
    state = _State(log)
    try:
        _run(str(requirement or ""), Path(project_dir), deadline, state, node_modules)
    except _OutOfTime:
        state.stop_all("out of time")
    except Exception as exc:
        state.stop_all(f"probe error {type(exc).__name__}")
        try:
            log(f"[{ITEM}] (diagnostic) crashed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
    try:
        return state.report()
    except Exception as exc:
        return {"item": ITEM, "status": NOT_RUN, "result": f"not run: report error {type(exc).__name__}"[:300],
                "findings": [], "parts": {p: NOT_RUN for p in PARTS}, "evidence": {}}

class _State:
    def __init__(self, log, item: str = ITEM, parts: tuple[str, ...] = PARTS) -> None:
        self.log, self.item, self.part_names = log, item, parts
        self.cap = 500 if item == ITEM else 1500
        self.parts: dict[str, tuple[str, str]] = {}
        self.findings: list[str] = []
        self.evidence: dict[str, Any] = {"calls": []}
        self.reason = ""

    def say(self, text: str) -> None:
        try:
            self.log(f"[{self.item}] {text}")
        except Exception:
            pass

    def set(self, part: str, status: str, note: str, finding: str | None = None) -> None:
        if part in self.parts:
            return
        self.parts[part] = (status, note)
        if finding and len(self.findings) < 5:
            self.findings.append(finding[:self.cap])
        self.say(f"{part}: {status} ({note})")

    def stop_all(self, reason: str) -> None:
        self.reason = self.reason or reason
        for part in self.part_names:
            self.set(part, NOT_RUN, reason)

    def call(self, label: str, method: str, path: str, status: int) -> None:
        calls = self.evidence["calls"]
        if len(calls) < 40:
            calls.append(f"{label} {method.upper()} {path} -> {status}")

    def report(self) -> dict:
        for part in self.part_names:
            self.parts.setdefault(part, (NOT_RUN, self.reason or "not reached"))
        statuses = {p: self.parts[p][0] for p in self.part_names}
        ran = [p for p in self.part_names if statuses[p] != NOT_RUN]
        if any(s == FINDING for s in statuses.values()):
            overall = FINDING
        elif ran:
            overall = CLEAN
        else:
            overall = NOT_RUN
        if overall == NOT_RUN:
            reasons = sorted({self.parts[p][1] for p in self.part_names})
            result = "not run: " + "; ".join(reasons)
        else:
            done = ", ".join(f"{p} {statuses[p]}" for p in ran)
            skipped = "; ".join(f"{p} not run: {self.parts[p][1]}" for p in self.part_names if p not in ran)
            result = f"{overall}: {done}" + (f" ({skipped})" if skipped else "")
        self.evidence["parts"] = {p: self.parts[p][1][:160] for p in self.part_names}
        return {"item": self.item, "status": overall, "result": result[:300], "findings": list(self.findings[:5]),
                "parts": statuses, "evidence": self.evidence}

def _run(requirement: str, project_dir: Path, deadline: float, st: _State, node_modules: str | None,
         mode: str = "startup") -> None:
    creds = sp.credentials(requirement) if requirement else []
    backend = project_dir / "backend"
    if not backend.is_dir():
        st.stop_all("no backend directory")
        return
    logins, _logouts = sp.backend_routes(project_dir)
    if not creds and not logins:
        st.stop_all("no sign-in: the requirement states no credentials and the backend has no sign-in route")
        return
    if not creds:
        st.stop_all("the requirement states no sign-in credentials")
        return
    if not logins:
        st.stop_all("no sign-in route found in the backend sources")
        return
    flat = " ".join(requirement.split())
    if sp.SINGLE_SESSION.search(flat):
        st.stop_all("the requirement limits an account to one session")
        return
    routes = session_routes(project_dir)
    front = sp.frontend_login_keys(project_dir)
    candidates = []
    for entry in logins:
        fkeys = [k for p, k in front if entry["path"].endswith(p.split("?")[0]) or p.endswith(entry["path"])]
        candidates.append({**entry, "front": fkeys})
    candidates.sort(key=lambda c: (not c["front"], len(c["path"])))
    whoami = sp.whoami_routes(project_dir)
    st.evidence["routes"] = {
        "sign_in": [c["path"] for c in candidates[:2]], "who_am_i": [w["path"] for w in whoami],
        "sign_out": [f"{r['method'].upper()} {r['path']}" for r in routes["logout"][:3]],
        "session_list": [r["path"] for r in routes["list"][:3]],
        "revoke": [f"{r['method'].upper()} {r['path']} ({r['kind']})" for r in routes["revoke"][:4]]}
    if not whoami:
        st.stop_all("no who-am-I GET route in the backend sources")
        return
    nm = node_modules or str(backend / "node_modules")
    if not os.path.isdir(os.path.join(nm, "express")):
        st.stop_all("no node_modules with express")
        return
    node = shutil.which("node")
    start = sp._start_file(backend)
    if not node or not start:
        st.stop_all("no node or no backend start file")
        return
    port = sp.pick_port()
    if not port:
        st.stop_all("no free port")
        return
    reserve = min(sp.CLEANUP_RESERVE, max(0.0, deadline - time.time()) * 0.25)
    work_end = deadline - reserve
    if work_end - time.time() < sp.MIN_START:
        st.stop_all("too little time to start the app")
        return
    from .fsutil import BackgroundProcess

    work = Path(tempfile.mkdtemp(prefix="arc-session-chain-"))
    server = None
    try:
        copy = work / "backend"
        try:
            sp._copy_backend(backend, copy, work_end)
        except sp._OutOfTime:
            st.stop_all("out of time while copying the backend")
            return
        dist = project_dir / "frontend" / "dist"
        if mode == "revoke" and (dist / "index.html").is_file():
            try:
                shutil.copytree(dist, work / "frontend" / "dist")
            except OSError:
                pass
        data = work / "data"
        data.mkdir()
        env = {"PORT": str(port), "NODE_PATH": nm, "DATA_DIR": str(data), "ARC_DB_FILE": str(data / "database.db"),
               "DATABASE_FILE": str(data / "database.db"), "NODE_ENV": "test"}
        server = BackgroundProcess([node, start], cwd=copy, env=env)
        base = f"http://127.0.0.1:{port}"
        while True:
            left = work_end - time.time()
            if left < 2 or not server.alive():
                st.stop_all("the app did not start")
                st.evidence["server_output"] = server.output_so_far()[-300:]
                return
            if _answers(base, min(2.0, left - 1)):
                break
            time.sleep(0.5)
        time.sleep(0.5)
        if not server.alive() or "EADDRINUSE" in server.output_so_far():
            st.stop_all(f"port {port} is held by another process")
            return
        st.evidence["port"] = port
        probe = _Probe(base, requirement, flat, creds, candidates, whoami, routes, work_end, st)
        if mode == "revoke":
            probe.work, probe.node, probe.nm, probe.project_dir = work, node, nm, project_dir
            probe.run_revoke()
        else:
            probe.run()
    finally:
        if server is not None:
            sp._stop(server, max(1.0, deadline - time.time() - 1))
            _kill_strays(server, port, deadline)
        while True:
            shutil.rmtree(work, ignore_errors=True)
            if not work.exists() or time.time() > deadline:
                break
            time.sleep(0.3)
        if work.exists():
            sp._remove_later(work)

def _answers(base: str, seconds: float) -> bool:
    req = urllib.request.Request(base + "/api/__arc_session_chain_liveness__", method="GET")
    try:
        urllib.request.build_opener(sp._NoRedirect).open(req, timeout=max(0.5, seconds))
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False

def _kill_strays(server, port: int, deadline: float) -> None:
    import subprocess

    from .fsutil import IS_WINDOWS

    if not IS_WINDOWS or not sp._port_taken(port):
        return
    pid = int(server.process.pid)
    command = ("Get-CimInstance Win32_Process -Filter \"ParentProcessId=%d\" | Where-Object { $_.Name -eq 'node.exe' } | "
               "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }") % pid
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True,
                       timeout=max(3.0, min(15.0, deadline - time.time())))
    except Exception:
        pass

class _Probe:
    def __init__(self, base, requirement, flat, creds, candidates, whoami, routes, deadline, st: _State) -> None:
        self.base, self.requirement, self.flat, self.creds = base, requirement, flat, creds
        self.candidates, self.whoami, self.routes, self.deadline, self.st = candidates, whoami, routes, deadline, st
        self.login: tuple[dict, dict] | None = None
        self.me: dict | None = None
        self.identity: list[str] = []
        self.ident_required = False
        self.revoked: _Jar | None = None
        self.keeper: _Jar | None = None
        self.revoke_call, self.revoked_status = "", 0
        self.revoke_route: dict | None = None
        self.work = self.node = self.nm = self.project_dir = None

    def req(self, label: str, method: str, path: str, body: dict | None, jar: _Jar | None) -> _Resp:
        resp = _http(self.base, method, path, body, jar, self.deadline)
        self.st.call(label, method, path, resp.status)
        return resp

    def sign_in(self, name: str) -> tuple[_Jar | None, int]:
        if self.login is not None:
            route, body = self.login
            last = 0
            for attempt in (1, 2):
                jar = _Jar(name)
                resp = self.req(f"sign-in {name}", "POST", route["path"], body, jar)
                last = resp.status
                if _ok(resp.status) and self._take(jar, resp):
                    return jar, last
                if resp.status != 429 or attempt == 2:
                    break
                time.sleep(min(2.0, max(0.0, self.deadline - time.time() - 2)))
            return None, last
        last = 0
        for round_ in (1, 2):
            for cand in self.candidates[:2]:
                for cred in self.creds[:4]:
                    for body in sp.body_variants(cred, cand["front"], cand["keys"]):
                        jar = _Jar(name)
                        resp = self.req(f"sign-in {name}", "POST", cand["path"], body, jar)
                        last = resp.status
                        if _ok(resp.status) and self._take(jar, resp):
                            self.login = (cand, body)
                            self.identity = [v for k, v in body.items() if not re.search(r"(?i)pass", k) and v]
                            for c in self.creds:
                                if c.get("password") in body.values() and (c.get("account") in body.values()
                                                                             or c.get("email") in body.values()):
                                    self.identity = [v for v in (c.get("account"), c.get("email")) if v]

                            self.identity = [v for v in self.identity if isinstance(v, str) and len(v) >= 3]
                            self.st.evidence["sign_in"] = {"route": cand["path"], "keys": sorted(body),
                                                           "account": self.identity[:1]}
                            return jar, last
                        if resp.status == 429:
                            return None, 429
            if round_ == 1:
                time.sleep(min(2.0, max(0.0, self.deadline - time.time() - 2)))
        return None, last

    @staticmethod
    def _take(jar: _Jar, resp: _Resp) -> bool:
        _apply(jar, resp)
        if not jar.cookies:
            jar.bearer = _token(resp.json())
        return not jar.empty()

    def view(self, label: str, jar: _Jar | None) -> tuple[str, int]:
        resp = self.req(label, "GET", self.me["path"], None, jar)
        return self.judge(resp), resp.status

    def judge(self, resp: _Resp) -> str:
        has_identity = self._has_identity(resp.text)
        if _ok(resp.status):
            if not self.ident_required or has_identity:
                return "signed"
            return "refused" if not has_identity else "unclear"
        if resp.status in (401, 403):
            return "refused"
        return "unclear"

    def _has_identity(self, text: str) -> bool:
        low = text.lower()
        return any(v.lower() in low for v in self.identity if v)

    def run(self) -> None:
        st = self.st
        jar1, status = self.sign_in("1")
        if jar1 is None:
            st.stop_all(f"no stated account signed in with a session (last answer {status}"
                        + (": rate limited" if status == 429 else "") + ")")
            return
        if not self.control(jar1):
            return
        jar2, status = self.sign_in("2")
        if jar2 is None:
            st.stop_all(f"the second sign-in got {status}" + (" (rate limited)" if status == 429 else "")
                        + "; two concurrent sessions are needed")
            return
        v1, s1 = self.view("me 1 after sign-in 2", jar1)
        v2, s2 = self.view("me 2", jar2)
        if v1 != "signed" or v2 != "signed":
            if v1 == "refused" and v2 == "signed":
                why = ("the requirement says a new sign-in ends the other sessions" if sp.ENDS_OTHERS.search(self.flat)
                       else "a second sign-in ended the first session (the sign-in probe reports this)")
            else:
                why = f"two concurrent sessions not both signed in (who-am-I {s1}/{s2})"
            st.stop_all(why)
            return
        self.isolation(jar1, jar2)
        self.revoke()

    def control(self, jar1: _Jar) -> bool:
        st = self.st
        seen = []
        for route in self.whoami:
            with_jar = self.req("control me", "GET", route["path"], None, jar1)
            without = self.req("control anon", "GET", route["path"], None, None)
            seen.append(f"{route['path']} {with_jar.status}/{without.status}")
            if not _ok(with_jar.status):
                continue
            carries = self._has_identity(with_jar.text)
            if without.status in (401, 403):
                self.me, self.ident_required = route, carries
            elif _ok(without.status) and carries and not self._has_identity(without.text):
                self.me, self.ident_required = route, True
            if self.me:
                st.evidence["who_am_i"] = {"route": route["path"], "signed": with_jar.status, "anonymous": without.status,
                                           "identity_in_body": carries}
                return True
        st.stop_all("no who-am-I contrast (signed-in/anonymous: " + ", ".join(seen)[:120] + ")")
        return False

    def isolation(self, jar1: _Jar, jar2: _Jar) -> None:
        st = self.st
        logouts = self.routes["logout"][:2]
        if not logouts:
            st.set("signout_isolation", NOT_RUN, "no sign-out route")
            st.set("inactive_rejected", NOT_RUN, "no sign-out route")
            return
        old1 = jar1.copy("1-old")
        out = bye = None
        for route in logouts:
            resp = self.req("sign-out 1", route["method"], route["path"], {} if route["method"] != "get" else None, jar1)
            if 200 <= resp.status < 400:
                out, bye = route, resp
                break
        if out is None:
            note = "sign-out did not answer 2xx/3xx (" + ", ".join(
                c for c in st.evidence["calls"] if c.startswith("sign-out"))[:100] + ")"
            st.set("signout_isolation", NOT_RUN, note)
            st.set("inactive_rejected", NOT_RUN, note)
            return
        _apply(jar1, bye)
        jar1.bearer = None
        sign_out = f"{out['method'].upper()} {out['path']} -> {bye.status}"
        st.evidence["sign_out"] = sign_out
        me = self.me["path"]
        browser1 = "refused" if jar1.empty() else self.view("me 1 after sign-out", jar1)[0]
        stale, stale_status = self.view("me 1 old credential", old1)
        other, other_status = self.view("me 2 after sign-out 1", jar2)
        if other == "refused":
            other, other_status = self.view("me 2 again", jar2)

        if other == "signed" and browser1 == "refused":
            st.set("signout_isolation", CLEAN, "")
        elif other == "refused" and browser1 == "refused":
            if SIGNOUT_ALL.search(self.flat):
                st.set("signout_isolation", NOT_RUN, "the requirement says sign-out ends every session")
            else:
                st.set("signout_isolation", FINDING, f"jar 2 refused ({other_status}) after jar 1 signed out", (
                    f"Signing out in one browser also ends another browser's session of the same account: two cookie "
                    f"jars signed in (GET {me} 2xx for both, {self.me_control()}); after jar 1 signed out ({sign_out}), "
                    f"GET {me} with jar 2 answered {other_status}. Fix: sign-out ends only the session identified by "
                    f"the request's own cookie/token (delete or deactivate WHERE token = the current one), never every "
                    f"session of the account or a shared session; each sign-in creates its own session record."))
        elif browser1 == "signed":
            st.set("signout_isolation", NOT_RUN, f"sign-out ({sign_out}) left jar 1 signed in")
        else:
            st.set("signout_isolation", NOT_RUN, f"unclear who-am-I answer for jar 2 ({other_status})")

        if stale == "refused":
            st.set("inactive_rejected", CLEAN, "")
        elif stale == "signed":
            only_cookie = browser1 == "refused"
            if only_cookie and not SERVER_END.search(self.flat):
                st.set("inactive_rejected", NOT_RUN, "sign-out only cleared the cookie; the requirement asks no "
                                                     "server-side end (not reported)")
            else:
                st.set("inactive_rejected", FINDING, f"the signed-out session still answers {stale_status}", (
                    f"A signed-out session still authenticates: after sign-out ({sign_out}) GET {me} with the cookie/token "
                    f"jar 1 held before signing out answered {stale_status} as the account"
                    + (" (the answer only cleared the browser cookie)" if only_cookie else "")
                    + ". Fix: sign-out deletes the stored session or marks it inactive, and the one authentication "
                      "middleware (and the who-am-I handler) accepts a session only when it exists AND is active, "
                      "answering 401 otherwise -- on every request."))
        else:
            st.set("inactive_rejected", NOT_RUN, f"unclear who-am-I answer for the old credential ({stale_status})")

    def me_control(self) -> str:
        w = self.st.evidence.get("who_am_i", {})
        return f"control: signed-in {w.get('signed')}, anonymous {w.get('anonymous')}"

    def end_session(self, route: dict, listing: dict | None, before: list[dict] | None, jar_a: _Jar, jar_b: _Jar,
                    tag: str) -> tuple[_Resp | None, str, str | None]:
        st, target = self.st, None
        if listing is not None:
            after = self.rows(listing, jar_b, f"list B after{tag}")
            mine = self.rows(listing, jar_a, f"list A{tag}")
            new = [i for i in _ids(after or []) if i not in _ids(before or [])] if before is not None and after else []
            current = [_rid(r) for r in (mine or []) if _is_current(r)]
            if len(current) == 1 and new and current[0] not in new:
                target = None
            else:
                target = current[0] if len(current) == 1 else (new[0] if len(new) == 1 else None)
            if target is not None:
                st.evidence["revoke_target" + tag.replace(" ", "_")] = ("current marker" if len(current) == 1
                                                                        else "new row")
        if route["kind"] != "others" and target is None:
            return None, ("could not tell session A's id from the session list"
                          + (f" GET {listing['path']}" if listing else "")), None
        path = route["path"]
        body: dict | None = {} if route["method"] != "get" else None
        if route["kind"] == "id":
            path = "/".join(target if (s.startswith(":") or s.startswith("{")) else s for s in path.split("/"))
        elif route["kind"] == "body":
            body = {k: target for k in route["keys"] if ID_KEY.search(k)}
        resp = self.req(f"revoke A{tag} from B", route["method"], path, body, jar_b)
        return resp, f"{route['method'].upper()} {route['path']} -> {resp.status}", target

    def revoke(self) -> None:
        st = self.st
        revokes = self.routes["revoke"]
        if not revokes:
            st.set("revoke", NOT_RUN, "no revoke route")
            return
        lists = self.routes["list"]
        usable = [r for r in revokes if r["kind"] == "others" or lists]
        if not usable:
            st.set("revoke", NOT_RUN, "a revoke route but no session-list route to find the other session's id")
            return
        route = usable[0]
        self.revoke_route = route
        jar_b, status = self.sign_in("B")
        if jar_b is None:
            st.set("revoke", NOT_RUN, f"sign-in B got {status}")
            return
        listing = lists[0] if lists else None
        before = self.rows(listing, jar_b, "list B before") if listing else None
        jar_a, status = self.sign_in("A")
        if jar_a is None:
            st.set("revoke", NOT_RUN, f"sign-in A got {status}")
            return
        va, sa = self.view("me A", jar_a)
        vb, sb = self.view("me B", jar_b)
        if va != "signed" or vb != "signed":
            st.set("revoke", NOT_RUN, f"sessions A/B not both signed in (who-am-I {sa}/{sb})")
            return
        resp, call, target = self.end_session(route, listing, before, jar_a, jar_b, "")
        if resp is None:
            st.set("revoke", NOT_RUN, call)
            return
        st.evidence["revoke"] = call
        if not (200 <= resp.status < 400):
            st.set("revoke", NOT_RUN, f"the revoke call was refused ({call})")
            return
        va, sa = self.view("me A after revoke", jar_a)
        vb, sb = self.view("me B after revoke", jar_b)
        if vb == "refused":
            vb, sb = self.view("me B again", jar_b)
        me = self.me["path"]
        if va == "refused" and vb == "signed":
            self.revoked, self.keeper, self.revoke_call, self.revoked_status = jar_a, jar_b, call, sa
            st.set("revoke", CLEAN, "")
            return
        targeted = route["kind"] == "id" or (route["kind"] == "others" and re.search(r"(?i)others?", route["path"]))
        if vb == "refused" and va in ("refused", "signed") and targeted:
            st.set("revoke", FINDING, f"revoking another session ended the current one ({sb})", (
                f"Revoking ANOTHER session also ends the current one: jar B revoked jar A's session ({call}); GET {me} "
                f"then answered jar B {sb} (jar A {sa}). Fix: the revoke action marks only the targeted session inactive "
                f"(by its id, for the signed-in account) and leaves the requesting browser's session active."))
            return
        if va == "signed" and vb == "signed":

            row_state = None
            if listing is not None and target is not None:
                rows = self.rows(listing, jar_b, "list B after revoke") or []
                match = [r for r in rows if _rid(r) == target]
                row_state = "removed" if rows and not match else ("inactive" if match and _inactive(match[0]) else
                                                                  "active" if match else None)
                st.evidence["revoked_row"] = row_state
            if row_state not in ("removed", "inactive"):
                st.set("revoke", NOT_RUN, f"revoke answered {resp.status}, A still signed in, but the session list does "
                                          "not confirm the revoke reached A (not reported)")
                return
            marked = "no longer lists it" if row_state == "removed" else "shows it inactive"
            st.set("revoke", FINDING, f"the revoked session still answers {sa}", (
                f"A revoked session keeps working: jar B revoked jar A's session ({call}) and the session list "
                f"{marked}, but GET {me} with jar A still answered {sa} as the account. Fix: the stored session carries "
                f"its state; revoke marks it inactive, and the ONE authentication middleware (and the who-am-I handler) "
                f"rejects an inactive session with 401 on every request, so that browser lands on sign-in at its next "
                f"request; the current session keeps working."))
            return
        st.set("revoke", NOT_RUN, f"unclear who-am-I answers after revoke (A {sa}, B {sb})")

    def run_revoke(self) -> None:
        st = self.st
        jar1, status = self.sign_in("1")
        if jar1 is None:
            st.stop_all(f"no stated account signed in with a session (last answer {status}"
                        + (": rate limited" if status == 429 else "") + ")")
            return
        if not self.control(jar1):
            return
        self.revoke()
        if st.parts.get("revoke", (NOT_RUN,))[0] != CLEAN or self.revoked is None:
            why = st.parts.get("revoke", (NOT_RUN, ""))
            st.set("revoked_page", NOT_RUN, "the revoke was not observed to end the session at the API"
                   + (f" (revoke {why[0]}: {why[1]})" if why[1] else f" (revoke {why[0]})"))
            return
        self.page_check()

    def page_check(self) -> None:
        st = self.st
        quote = revoke_redirect_quote(self.requirement)
        if not quote:
            st.set("revoked_page", NOT_RUN, "the requirement does not state that the revoked browser returns to the "
                                            "sign-in page")
            return
        if self.revoked.bearer and not self.revoked.cookies:
            st.set("revoked_page", NOT_RUN, "the session is a bearer token (no cookie a browser context can carry)")
            return
        nm = Path(str(self.nm or ""))
        if not (nm / "@playwright" / "test").is_dir():
            st.set("revoked_page", NOT_RUN, "no @playwright/test in node_modules")
            return
        if self.work is None or not (Path(self.work) / "frontend" / "dist" / "index.html").is_file():
            st.set("revoked_page", NOT_RUN, "no built frontend (frontend/dist/index.html)")
            return
        front = _front_text(Path(self.project_dir)) if self.project_dir else ""
        login = _front_route(front, LOGIN_ROUTE)
        if not login:
            st.set("revoked_page", NOT_RUN, "no sign-in page route in the frontend sources")
            return
        recipe = _signin_recipe(self.requirement)
        lacking = [k for k in ("entry", "identity", "password") if not recipe.get(k)]
        if lacking:
            st.set("revoked_page", NOT_RUN, "the requirement does not state how to sign in through the UI (no quoted "
                                            + "/".join(lacking) + ")")
            return
        account = self.ui_account(recipe)
        if account is None:
            st.set("revoked_page", NOT_RUN, "no stated account for the UI sign-in")
            return
        urls = ["/"]
        sessions_page = _front_route(front, SESSIONS_ROUTE)
        if sessions_page and sessions_page not in urls:
            urls.append(sessions_page)
        left = self.deadline - time.time()
        if left < 35:
            st.set("revoked_page", NOT_RUN, "too little time for the browser check")
            return
        route, jar_b = self.revoke_route, self.keeper
        if route is None or jar_b is None or self.view("me B before the page check", jar_b)[0] != "signed":
            st.set("revoked_page", NOT_RUN, "the revoking session B is no longer signed in")
            return
        listing = self.routes["list"][0] if self.routes["list"] else None
        before = self.rows(listing, jar_b, "list B before the browser sign-in") if listing else None
        cfg = {"base": self.base, "login": login, "urls": urls, "identity": [v for v in self.identity if v],
               "keeper": sorted(jar_b.cookies.items()), "signin": recipe, "account": account,
               "wait_ms": 6000, "deadline": int((time.time() + left - 4) * 1000)}
        end = time.time() + left - 2
        runner = _PageRunner(self.node, Path(self.work), str(nm))
        done = False
        try:
            done = self._page_chain(runner, cfg, end, route, listing, before, jar_b, login, quote)
        finally:
            runner.close(done)

    def _page_chain(self, runner: "_PageRunner", cfg: dict, end: float, route: dict, listing: dict | None,
                    before: list[dict] | None, jar_b: _Jar, login: str, quote: str) -> bool:
        st = self.st
        if not runner.send(cfg):
            st.set("revoked_page", NOT_RUN, f"the browser check did not run: {runner.error or 'no runner'}"[:200])
            return False
        first = runner.read(end)
        st.evidence["page"] = {k: first.get(k) for k in ("control", "visits", "signin", "error") if k in first}
        if first.get("error") or not isinstance(first.get("control"), dict):
            st.set("revoked_page", NOT_RUN, f"the browser check did not run: {str(first.get('error'))[:160]}")
            return bool(first.get("error")) and first.get("stage") == "error"
        if not first["control"].get("form"):
            st.set("revoked_page", NOT_RUN, f"no password field on the sign-in page {login} (control)")
            return runner.stop()
        judged, notes = [], []
        for visit in first.get("visits") or []:
            keeper, anon = visit.get("keeper") or {}, visit.get("anon") or {}
            workspace = not keeper.get("form") and (
                (keeper.get("identity") and not anon.get("identity"))
                or (anon.get("form") and not keeper.get("form"))
                or (not keeper.get("signInLink") and anon.get("signInLink")))
            if workspace:
                judged.append(visit)
            else:
                notes.append(f"{visit.get('path')}: the active session's page shows no signed-in contrast")
        signin = first.get("signin") or {}
        if not signin.get("ok"):
            st.set("revoked_page", NOT_RUN, "the UI sign-in of browser A did not complete: "
                   + str(signin.get("note") or "no signed-in page after submitting")[:160])
            return runner.stop()
        if not judged:
            st.set("revoked_page", NOT_RUN, "; ".join(notes)[:200] or "no page judged")
            return runner.stop()
        jar_a = _Jar("A (browser)")
        jar_a.cookies = {str(c[0]): str(c[1]) for c in first.get("cookies") or [] if isinstance(c, list) and len(c) == 2}
        if not jar_a.cookies:
            st.set("revoked_page", NOT_RUN, "browser A holds no cookie after the UI sign-in (the session lives in page "
                                            "storage; the API cannot target it)")
            return runner.stop()
        va, sa = self.view("me A (browser)", jar_a)
        if va != "signed":
            st.set("revoked_page", NOT_RUN, f"browser A's cookies are not signed in at who-am-I ({sa}) after the UI "
                                            "sign-in")
            return runner.stop()
        visit = judged[0]
        path = visit["path"]
        if not runner.send({"cmd": "park", "url": path}):
            st.set("revoked_page", NOT_RUN, "the browser runner stopped before the revoke")
            return False
        parked = runner.read(end)
        st.evidence["page"]["parked"] = parked.get("look") or parked.get("error")
        look = parked.get("look") or {}
        if parked.get("error") or look.get("form") or not (look.get("identity") or not look.get("signInLink")):
            st.set("revoked_page", NOT_RUN, f"browser A does not show the signed-in state on {path} before the revoke"
                   + (f" ({str(parked.get('error'))[:120]})" if parked.get("error") else ""))
            return runner.stop()
        resp, call, _target = self.end_session(route, listing, before, jar_a, jar_b, " browser")
        if resp is None:
            st.set("revoked_page", NOT_RUN, call)
            return runner.stop()
        st.evidence["revoke_browser"] = call
        if not (200 <= resp.status < 400):
            st.set("revoked_page", NOT_RUN, f"the revoke of browser A's session was refused ({call})")
            return runner.stop()
        va, sa = self.view("me A (browser) after revoke", jar_a)
        vb, sb = self.view("me B after the browser revoke", jar_b)
        if vb == "refused":
            vb, sb = self.view("me B again", jar_b)
        if va != "refused" or vb != "signed":
            st.set("revoked_page", NOT_RUN, f"after revoking browser A at the API: who-am-I A {sa}, B {sb} (not a "
                                            "clean revoke; the page is not judged)")
            return runner.stop()
        if not runner.send({"cmd": "reload"}):
            st.set("revoked_page", NOT_RUN, "the browser runner stopped before the reload")
            return False
        last = runner.read(end)
        st.evidence["page"]["reloaded"] = last.get("look") or last.get("error")
        seen_look = last.get("look")
        if last.get("error") or not isinstance(seen_look, dict):
            st.set("revoked_page", NOT_RUN, f"the reload of browser A did not finish: {str(last.get('error'))[:160]}")
            return False
        if seen_look.get("form"):
            st.set("revoked_page", CLEAN, f"sign-in page on reload of {path} (final URL {_path_of(seen_look.get('url'))})")
            return True
        seen = (f"{path} -> final URL {_path_of(seen_look.get('url'))}, no password field after "
                f"{cfg['wait_ms'] // 1000} s" + (", a 'Sign in' link on the page" if seen_look.get("signInLink") else "")
                + (", the account name still shown" if seen_look.get("identity") else ""))
        me = self.me["path"]
        how = (f'home -> "{cfg["signin"]["entry"]}" -> "{cfg["signin"]["identity"]}" / "{cfg["signin"]["password"]}" '
               f'-> "{cfg["signin"].get("submit") or cfg["signin"]["entry"]}"')
        st.set("revoked_page", FINDING, f"revoked browser not on the sign-in page ({seen})"[:200], (
            f"A revoked browser is not sent to the sign-in page: a real browser signed in through the app's own sign-in "
            f"form ({how}) and showed the signed-in page {path}; jar B revoked that browser's session ({call}) and "
            f"GET {me} then refused that browser's cookie ({sa}) while jar B stayed signed in -- but reloading that SAME "
            f"browser shows no sign-in form ({seen}); the sign-in page {login} shows one, and the same page with jar "
            f"B's cookie shows the signed-in workspace. Requirement: \"{quote}\". Fix: when a browser presents a session "
            f"the server no longer accepts (revoked / inactive), its next page load or reload of a workspace page lands "
            f"on the sign-in page {login} (e.g. the frontend's one session guard navigates to {login} when the "
            f"who-am-I call answers 401 for a session that had been signed in, or the server answers that page request "
            f"with a redirect to {login} and clears the cookie); a visitor who never signed in keeps the public pages "
            f"and their 'Sign in' link as before; the other session keeps working."))
        return True

    def ui_account(self, recipe: dict) -> dict | None:
        if self.login is None:
            return None
        body = self.login[1]
        label = str(recipe.get("identity") or "").lower()
        for cred in self.creds:
            pw, account, email = cred.get("password"), cred.get("account"), cred.get("email")
            if not pw or pw not in body.values() or not (account in body.values() or email in body.values()):
                continue
            if "mail" in label and "user" not in label and email:
                ident = email
            elif "mail" not in label and account:
                ident = account
            else:
                ident = email if email in body.values() else account
            return {"identity": ident, "password": pw} if ident else None
        return None

    def rows(self, listing: dict, jar: _Jar, label: str) -> list[dict] | None:
        resp = self.req(label, "GET", listing["path"], None, jar)
        if not _ok(resp.status):
            return None
        rows = _rows(resp.json())
        return rows if rows and all(_rid(r) is not None for r in rows) else None

def _rows(obj: Any, depth: int = 0) -> list[dict] | None:
    if isinstance(obj, list):
        if obj and all(isinstance(r, dict) for r in obj) and any(_rid(r) is not None for r in obj):
            return obj
        return [] if not obj and depth else None
    if isinstance(obj, dict) and depth < 2:
        for value in obj.values():
            found = _rows(value, depth + 1)
            if found:
                return found
    return None

def _rid(row: dict) -> str | None:
    for key in ID_KEYS:
        value = row.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value):
            return str(value)
    return None

def _ids(rows: list[dict]) -> list[str]:
    return [i for i in (_rid(r) for r in rows if not _inactive(r)) if i is not None]

def _is_current(row: dict) -> bool:
    return any(row.get(k) is True or row.get(k) in (1, "true", "yes") for k in CURRENT_KEYS)

def _inactive(row: dict) -> bool:
    for key in ("active", "is_active", "isActive"):
        if key in row and row[key] in (False, 0, "false", "0"):
            return True
    for key in ("status", "state"):
        value = row.get(key)
        if isinstance(value, str) and re.search(r"(?i)revok|inactive|ended|expired|terminated|signed.?out", value):
            return True
    return any(row.get(k) for k in ("revoked", "revoked_at", "revokedAt", "ended_at", "endedAt"))

REVOKE_ITEM = "session_revoke"
REVOKE_PARTS = ("revoke", "revoked_page")
SETTING = "session_revoke_after_batch"

REVOKE_STATED = re.compile(
    r"(?i)\brevok|\b(?:sign|log)(?:s|ged)?[- ]?out\s+(?:of\s+)?(?:other|every|all)\s+(?:sessions?|devices?|browsers?)\b|"
    r"\bend(?:s|ed)?\s+(?:the\s+)?other\s+sessions?\b")
REDIRECT_SIGNIN = re.compile(
    r"(?i)\b(?:redirect\w*|return\w*|sent|send\w*|land\w*|taken|go(?:es)?(?:\s+back)?)\s+(?:back\s+)?to\s+(?:the\s+)?"
    r"(?:sign[- ]?in|log[- ]?in)(?:\s+(?:page|screen|form))?\b")
LOGIN_ROUTE = re.compile(r"(?i)^/(?:[\w-]+/)*(?:log-?in|sign-?in)$")
SESSIONS_ROUTE = re.compile(r"(?i)^/(?:[\w-]+/)*sessions?$")
FRONT_PATH = re.compile(r"""\bpath\s*[:=]\s*\{?\s*["'`](/[\w/-]*)["'`]""")

def revoke_wanted(text: str) -> bool:
    return bool(REVOKE_STATED.search(" ".join(str(text or "").split())))

def revoke_redirect_quote(requirement: str) -> str:
    flat = " ".join(str(requirement or "").split())
    for sentence in re.split(r"(?<=[.!?])\s+|\s+-\s+keyword:", flat):
        if re.search(r"(?i)\brevok", sentence) and REDIRECT_SIGNIN.search(sentence):
            return sentence.strip(" '\"")[:300]
    return ""

def _front_route(front: str, pattern: re.Pattern) -> str | None:
    found = sorted({m.group(1) for m in FRONT_PATH.finditer(front or "") if pattern.match(m.group(1))}, key=len)
    return found[0] if found else None

def _path_of(url: Any) -> str:
    m = re.match(r"^[a-z]+://[^/]+(/.*)?$", str(url or ""))
    return (m.group(1) or "/") if m else str(url or "")[:80]

def _signin_recipe(requirement: str) -> dict:
    try:
        from .when_plan import Context

        recipe = Context(str(requirement or "")).signin
        return {k: recipe.get(k) for k in ("entry", "entry_role", "identity", "password", "submit", "submit_role")}
    except Exception:
        return {}

PAGE_RUNNER = r'''
const {chromium}=require('@playwright/test');
const readline=require('readline');
const rl=readline.createInterface({input:process.stdin});
const queue=[];let waiter=null;
rl.on('line',l=>{if(waiter){const w=waiter;waiter=null;w(l)}else queue.push(l)});
rl.on('close',()=>{if(waiter){const w=waiter;waiter=null;w(null)}else queue.push(null)});
const next=()=>queue.length?Promise.resolve(queue.shift()):new Promise(r=>{waiter=r});
const cmd=async()=>{const l=await next();try{return JSON.parse(l||'{}')}catch(e){return {}}};
const say=o=>process.stdout.write(JSON.stringify(o)+'\n');
async function look(page,cfg,waitForm){
 const t0=Date.now();let form=false;
 for(;;){
  form=await page.locator('input[type=password]').filter({visible:true}).count().then(n=>n>0).catch(()=>false);
  if(form||!waitForm||Date.now()-t0>cfg.wait_ms||Date.now()>cfg.deadline-3000)break;
  await new Promise(z=>setTimeout(z,250));
 }
 const text=await page.evaluate(()=>(document.body&&document.body.innerText)||'').catch(()=>'');
 const low=text.toLowerCase();
 const links=await page.getByRole('link',{name:/^(sign|log) ?in$/i}).filter({visible:true}).count().catch(()=>0);
 return {url:page.url(),form,identity:cfg.identity.some(v=>low.includes(String(v).toLowerCase())),chars:text.length,signInLink:links};
}
async function go(page,url,cfg){
 await page.goto(cfg.base+url,{waitUntil:'domcontentloaded',timeout:15000});
 try{await page.waitForLoadState('networkidle',{timeout:5000})}catch(e){}
}
async function field(page,name){
 const t0=Date.now();
 for(;;){
  for(const exact of [true,false]){
   for(const l of [page.getByLabel(name,{exact}),page.getByRole('textbox',{name,exact})]){
    const v=l.filter({visible:true});const n=await v.count().catch(()=>0);
    if(n===1||(exact&&n>0))return v.first();
   }
  }
  if(Date.now()-t0>8000)return null;
  await page.waitForTimeout(250);
 }
}
async function signIn(ctx,cfg){
 const s=cfg.signin;const p=await ctx.newPage();
 await go(p,'/',cfg);
 const roles=s.entry_role?[s.entry_role]:['link','button'];let entry=null;
 for(const role of roles){
  const l=p.getByRole(role,{name:s.entry,exact:true}).filter({visible:true});
  if(await l.count().catch(()=>0)){entry=l.first();break}
 }
 if(!entry)return {page:p,ok:false,note:'no visible '+roles.join('/')+' named "'+s.entry+'" on the home page'};
 await entry.click({timeout:10000});
 const who=await field(p,s.identity);
 if(!who)return {page:p,ok:false,note:'no field labelled "'+s.identity+'" after clicking "'+s.entry+'" ('+p.url()+')'};
 const pw=await field(p,s.password);
 if(!pw)return {page:p,ok:false,note:'no field labelled "'+s.password+'" ('+p.url()+')'};
 await who.fill(cfg.account.identity);await pw.fill(cfg.account.password);
 const submit=s.submit||s.entry;
 const btn=p.getByRole(s.submit_role||'button',{name:submit,exact:true}).filter({visible:true});
 if(!await btn.count().catch(()=>0))return {page:p,ok:false,note:'no visible button named "'+submit+'" on the sign-in form'};
 await btn.first().click({timeout:10000});
 const t0=Date.now();let seen=null;
 for(;;){
  await p.waitForTimeout(400);
  seen=await look(p,cfg,false);
  if(!seen.form&&(seen.identity||!seen.signInLink))break;
  if(Date.now()-t0>10000||Date.now()>cfg.deadline-15000)break;
 }
 try{await p.waitForLoadState('networkidle',{timeout:5000})}catch(e){}
 const ok=!seen.form&&(seen.identity||!seen.signInLink);
 return {page:p,ok,look:seen,note:ok?'':'still '+(seen.form?'a password field':'a "Sign in" link and no account name')+' 10 s after submitting ('+seen.url+')'};
}
async function run(){
 let browser;
 try{
  const cfg=await cmd();
  browser=await chromium.launch({headless:true});
  const open=async cookies=>{const ctx=await browser.newContext({viewport:{width:1280,height:800}});
   if(cookies.length)await ctx.addCookies(cookies.map(c=>({name:c[0],value:c[1],url:cfg.base})));return ctx};
  const anon=await open([]);const p0=await anon.newPage();
  await go(p0,cfg.login,cfg);const control=await look(p0,cfg,true);const visits=[];
  for(const url of cfg.urls){
   if(Date.now()>cfg.deadline-30000)break;
   const v={path:url};visits.push(v);
   await go(p0,url,cfg);await p0.waitForTimeout(1000);v.anon=await look(p0,cfg,false);
   const k=await open(cfg.keeper);const pk=await k.newPage();await go(pk,url,cfg);await pk.waitForTimeout(1000);
   v.keeper=await look(pk,cfg,false);await k.close();
  }
  await anon.close();
  const a=await open([]);
  const si=await signIn(a,cfg);
  const cookies=(await a.cookies()).map(c=>[c.name,c.value]);
  say({stage:'signed',control,visits,signin:{ok:si.ok,note:si.note,look:si.look||null,url:si.page.url()},cookies});
  if(!si.ok)return;
  let c=await cmd();
  if(c.cmd!=='park')return;
  await go(si.page,c.url,cfg);await si.page.waitForTimeout(1000);
  say({stage:'parked',look:await look(si.page,cfg,false)});
  c=await cmd();
  if(c.cmd!=='reload')return;
  await si.page.reload({waitUntil:'domcontentloaded',timeout:15000});
  try{await si.page.waitForLoadState('networkidle',{timeout:5000})}catch(e){}
  say({stage:'reloaded',look:await look(si.page,cfg,true)});
 }catch(e){say({stage:'error',error:String(e).slice(0,300)})}
 finally{if(browser)await browser.close().catch(()=>{});rl.close();process.stdin.destroy()}
}
run();
'''

class _PageRunner:

    def __init__(self, node: Any, work: Path, nm: str, command: list[str] | None = None) -> None:
        import queue
        import subprocess
        import threading

        self.proc = None
        self.error = ""
        self.err_tail: list[str] = []
        self.lines: "queue.Queue[str | None]" = queue.Queue()
        try:
            if command is None:
                if not node:
                    self.error = "no node"
                    return
                script = work / "session-revoke-page.cjs"
                script.write_text(PAGE_RUNNER, encoding="utf-8")
                command = [str(node), str(script)]
            env = {**os.environ, "NODE_PATH": nm}
            if os.name == "nt":
                flags = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
            else:
                flags = {"start_new_session": True}
            self.proc = subprocess.Popen(command, cwd=str(work), env=env, stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                         errors="replace", **flags)
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {str(exc)[:160]}"
            self.proc = None
            return
        try:
            from .fsutil import register_child

            register_child(self.proc, own_group=os.name != "nt")
        except Exception:
            pass
        threading.Thread(target=self._pump, args=(self.proc.stdout, True), daemon=True).start()
        threading.Thread(target=self._pump, args=(self.proc.stderr, False), daemon=True).start()

    def _pump(self, stream, is_out: bool) -> None:
        try:
            for line in stream:
                if is_out:
                    self.lines.put(line)
                else:
                    self.err_tail.append(line)
                    del self.err_tail[:-20]
        except Exception:
            pass
        finally:
            if is_out:
                self.lines.put(None)

    def send(self, obj: dict) -> bool:
        if self.proc is None:
            return False
        try:
            self.proc.stdin.write(json.dumps(obj) + "\n")
            self.proc.stdin.flush()
            return True
        except Exception as exc:
            self.error = f"{type(exc).__name__}"
            return False

    def stop(self) -> bool:
        self.send({"cmd": "stop"})
        return True

    def read(self, until: float) -> dict:
        import queue

        if self.proc is None:
            return {"error": self.error or "no runner"}
        while True:
            left = until - time.time()
            if left <= 0:
                return {"error": "the browser check overran its time"}
            try:
                line = self.lines.get(timeout=left)
            except queue.Empty:
                return {"error": "the browser check overran its time"}
            if line is None:
                return {"error": "the browser runner ended without an answer: " + "".join(self.err_tail)[-200:]}
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                return obj

    def close(self, orderly: bool, grace: float = 8.0) -> None:
        proc = self.proc
        if proc is None:
            return
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=grace if orderly else 0.5)
        except Exception:
            pass
        if proc.poll() is None or not orderly:
            _end_tree(proc)
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        try:
            from .fsutil import unregister_child

            unregister_child(proc)
        except Exception:
            pass

def _end_tree(proc) -> None:
    import subprocess

    from .fsutil import IS_WINDOWS, _kill_tree

    try:
        if IS_WINDOWS and proc.poll() is not None:
            command = ("Get-CimInstance Win32_Process -Filter \"ParentProcessId=%d\" | Where-Object { $_.Name -match "
                       "'^(chrome|chrome-headless-shell|headless_shell|msedge|node)\\.exe$' } | ForEach-Object { "
                       "taskkill /PID $_.ProcessId /T /F | Out-Null }") % int(proc.pid)
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True,
                           timeout=20)
            return
        _kill_tree(proc, own_group=not IS_WINDOWS, timeout=15.0)
    except Exception:
        pass

def revoke_probe(project_dir: Path, requirement_text: str, *, time_limit: float = 150.0, log=print,
                 node_modules: str | None = None, deadline: float | None = None) -> dict:
    end = float(deadline) if deadline is not None else time.time() + max(0.0, float(time_limit or 0))
    state = _State(log, REVOKE_ITEM, REVOKE_PARTS)
    try:
        _run(str(requirement_text or ""), Path(project_dir), end, state, node_modules, mode="revoke")
    except _OutOfTime:
        state.stop_all("out of time")
    except Exception as exc:
        state.stop_all(f"probe error {type(exc).__name__}")
        try:
            log(f"[{REVOKE_ITEM}] (diagnostic) crashed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
    try:
        return state.report()
    except Exception as exc:
        return {"item": REVOKE_ITEM, "status": NOT_RUN, "result": f"not run: report error {type(exc).__name__}"[:300],
                "findings": [], "parts": {p: NOT_RUN for p in REVOKE_PARTS}, "evidence": {}}

def _leaf_texts(whole: str, ids: list[str]) -> str:
    wanted = {str(i) for i in ids or []}
    out: list[str] = []
    try:
        import yaml

        tree = yaml.safe_load(whole)
    except Exception:
        tree = None

    def walk(obj: Any) -> None:
        if isinstance(obj, list):
            for item in obj:
                walk(item)
        elif isinstance(obj, dict):
            if str(obj.get("id")) in wanted:
                out.append(f"{obj.get('name') or ''}. {obj.get('description') or ''}")
                for scen in obj.get("scenarios") or []:
                    for step in (scen.get("steps") or []) if isinstance(scen, dict) else []:
                        if isinstance(step, dict):
                            out.append(str(step.get("content") or ""))
            for value in obj.values():
                if isinstance(value, (list, dict)):
                    walk(value)

    walk(tree)
    return "\n".join(out)

def batch_findings(project_dir: Path, whole_requirement: str, member_ids: list[str], *, time_limit: float = 150.0,
                   log=print, node_modules: str | None = None) -> list[dict[str, str]]:
    try:
        from .settings import setting_on

        if not setting_on(SETTING, True):
            return []
        own = _leaf_texts(whole_requirement, member_ids)
        if not revoke_wanted(own):
            return []
        nm = node_modules or str(Path(project_dir) / "backend" / "node_modules")
        res = revoke_probe(project_dir, whole_requirement, time_limit=time_limit, log=log, node_modules=nm)
        try:
            log(f"[{REVOKE_ITEM}] after batch {','.join(map(str, member_ids))}: {res.get('result')}")
        except Exception:
            pass
        quote = revoke_redirect_quote(own) or revoke_redirect_quote(whole_requirement)
        out = []
        for text in res.get("findings") or []:
            problem, _, fix = str(text).partition(" Fix: ")
            out.append({"where": f"{REVOKE_ITEM} probe (two browser sessions, live)",
                        "requirement": quote or "(revoking a session ends it: the revoked browser is refused)",
                        "problem": problem.strip(), "fix": fix.strip() or "see the problem"})
        return out
    except Exception as exc:
        try:
            log(f"[{REVOKE_ITEM}] (diagnostic) after-batch probe crashed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return []
