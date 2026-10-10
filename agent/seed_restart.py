from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

from . import fsutil
from .seed_identity import HARNESS_JS, _backend_dir, parse_seed_names, requirement_texts

CHECK = "restart"
MAX_FINDINGS = 8
CLEANUP_RESERVE = 3.0
FIX = ("Seed each record only when a SELECT by its natural key (e.g. the name, or parent id + name) finds nothing, or "
       "declare that key UNIQUE so INSERT OR IGNORE skips it; CREATE TABLE/INDEX IF NOT EXISTS and add a column only "
       "when PRAGMA table_info lacks it. Keep exactly the seeded records and values the requirement states. Starting "
       "the server again on an existing database must leave the data exactly as it was.")

TIMESTAMP_COL = re.compile(r"(?i)^(?:(?:date_|last_)?(?:created|updated|modified|inserted|seeded|changed)(?:_?(?:at|on|time|date|ts))?"
                           r"|timestamp|ts|last_?updated|last_?modified|mtime|ctime)$")
UUID = re.compile(r"(?i)^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32})$")

GROWING_TABLE = re.compile(r"(?i)(?:^|_)(?:logs?|audit\w*|sessions?|tokens?|migrations?|jobs?|queues?|metrics?|"
                           r"telemetry|heartbeats?)(?:$|_)")
TRANSIENT = re.compile(r"(?i)timeout|SQLITE_BUSY|SQLITE_LOCKED|database is locked|handle is closed|EADDRINUSE|"
                       r"SQLITE_MISUSE|ECONNREFUSED|ENOENT|EPERM|EBUSY|out of memory")
INIT_DONE = re.compile(r"\[harness\] (?:initializeDatabase done|runtime init done)")
LITERAL = re.compile(r"`([^`]{1,80})`|\"([^\"]{1,80})\"|“([^”]{1,80})”")
SKIP_DIRS = {"node_modules", "dist", "test-e2e", "tests", "test", "__tests__"}

class _Deadline(Exception):
    pass

def _given_texts(node, out: list[str]) -> None:
    if isinstance(node, dict):
        if str(node.get("keyword", "")).strip().upper() == "GIVEN" and isinstance(node.get("content"), str):
            out.append(re.sub(r"\s+", " ", node["content"]))
        for v in node.values():
            _given_texts(v, out)
    elif isinstance(node, list):
        for v in node:
            _given_texts(v, out)

def seed_names(requirement: str) -> set[str]:
    if not requirement or not requirement.strip():
        return set()
    names = {n for (_, n) in parse_seed_names(requirement)}
    texts: list[str] = []
    try:
        import yaml
        tree = yaml.safe_load(requirement)
        if isinstance(tree, (dict, list)):
            _given_texts(tree, texts)
    except Exception:
        pass
    texts += [ln for ln in requirement.splitlines() if re.match(r"(?i)^\s*(?:-\s*)?given\b", ln)]
    for text in requirement_texts(requirement):
        texts += [s for s in re.split(r"(?<=[.!?])\s+", text) if re.search(r"(?i)\bseed", s)]
    for text in texts:
        for m in LITERAL.finditer(text):
            lit = next(g for g in m.groups() if g is not None).strip()
            if lit:
                names.add(lit)
    return names

def _tables(con) -> dict[str, list[tuple[str, str, int]]]:
    out = {}
    for (name,) in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'"):
        out[name] = [(r[1], (r[2] or "").upper(), int(r[5] or 0)) for r in con.execute(f'pragma table_info("{name}")')]
    return out

def _uuid_column(cons, table: str, column: str) -> bool:
    seen = 0
    for con in cons:
        for (v,) in con.execute(f'select "{column}" from "{table}"'):
            if not isinstance(v, str) or not UUID.match(v.strip()):
                return False
            seen += 1
    return seen > 0

def _key_columns(cons, table: str, cols: list[tuple[str, str, int]]) -> list[str]:
    pks = [c for c in cols if c[2] > 0]
    ignored = set()
    if len(pks) == 1:
        name, ctype, _ = pks[0]
        if "INT" in ctype or _uuid_column(cons, table, name):
            ignored.add(name)
    for name, _, _ in cols:
        if TIMESTAMP_COL.match(name):
            ignored.add(name)
    return [c[0] for c in cols if c[0] not in ignored]

def _groups(con, table: str, keys: list[str]) -> dict[tuple, int]:
    sel = ", ".join(f'"{k}"' for k in keys)
    res: dict[tuple, int] = {}
    for row in con.execute(f'select {sel} from "{table}"'):
        res[tuple(row)] = res.get(tuple(row), 0) + 1
    return res

def _short(keys: list[str], values: tuple) -> str:
    parts = []
    for k, v in zip(keys, values):
        if v is None:
            continue
        s = repr(v) if isinstance(v, str) else str(v)
        parts.append(f"{k}={s[:40]}")
    text = ", ".join(parts)
    return text if len(text) <= 160 else text[:157] + "..."

def compare_snapshots(db_a: str, db_b: str, deadline: float | None = None, names: set[str] | None = None) -> list[dict]:
    ca = sqlite3.connect(f"file:{db_a}?mode=ro", uri=True)
    cb = sqlite3.connect(f"file:{db_b}?mode=ro", uri=True)
    try:
        ta, tb = _tables(ca), _tables(cb)
        out = []
        for table, cols in sorted(tb.items()):
            if deadline is not None and time.time() > deadline:
                raise _Deadline()
            if table not in ta or GROWING_TABLE.search(table):
                continue
            if [c[0] for c in ta[table]] != [c[0] for c in cols]:
                continue
            na = ca.execute(f'select count(*) from "{table}"').fetchone()[0]
            nb = cb.execute(f'select count(*) from "{table}"').fetchone()[0]
            if nb <= na:
                continue
            keys = _key_columns((ca, cb), table, cols)
            if not keys:
                continue
            ga, gb = _groups(ca, table, keys), _groups(cb, table, keys)
            dups = [(vals, ga.get(vals, 0), n) for vals, n in gb.items()
                    if n >= 2 and n > ga.get(vals, 0) >= 1 and any(isinstance(v, str) and v.strip() for v in vals)]
            if not dups:
                continue

            def named(vals):
                return next((v.strip() for v in vals if isinstance(v, str) and names and v.strip() in names), None)
            dups.sort(key=lambda d: (named(d[0]) is None, -d[2], str(d[0])))
            vals, n_a, n_b = dups[0]
            out.append({"table": table, "count_a": na, "count_b": nb, "copies_a": n_a, "copies_b": n_b,
                        "records": len(dups), "sample": _short(keys, vals), "seed_name": named(vals),
                        "seed_names": sorted({x for x in (named(d[0]) for d in dups) if x}),
                        "ignored": [c[0] for c in cols if c[0] not in keys]})
        return out
    finally:
        ca.close()
        cb.close()

def _source_files(backend: Path):
    src = backend / "src"
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(".js") and ".spec." not in f and ".test." not in f:
                yield Path(root) / f

def _insert_site(backend: Path, project: Path, table: str) -> tuple[str, str] | None:
    pat = re.compile(r"(?i)\binsert\s+(?:or\s+\w+\s+)?into\s+[`\"'\[]?" + re.escape(table) + r"\b")
    files = sorted(_source_files(backend), key=lambda p: (0 if "database" in p.parts else 1, str(p)))
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            if pat.search(line):
                return f"{path.relative_to(project).as_posix()}:{i}", line.strip().rstrip(",;")[:160]
    return None

def _default_where(backend: Path, project: Path) -> str:
    for name in ("init_db.js", "db_runtime.js"):
        p = backend / "src" / "database" / name
        if p.is_file():
            return f"{p.relative_to(project).as_posix()}:1"
    return "backend/src:1"

def _stack_site(text: str, copy_dir: str, backend: Path, project: Path) -> str | None:
    norm = text.replace("\\", "/")
    base = copy_dir.replace("\\", "/").rstrip("/") + "/"
    for m in re.finditer(re.escape(base) + r"([^\s():]+\.js):(\d+)", norm, re.I):
        rel = m.group(1)
        if "node_modules" in rel:
            continue
        p = backend / rel
        if p.is_file():
            return f"{p.relative_to(project).as_posix()}:{m.group(2)}"
    return None

def _failure_line(text: str) -> str | None:
    m = re.search(r"\[harness\] FAILED: (.+)", text)
    return m.group(1).strip()[:200] if m else None

def _stop(process: subprocess.Popen, budget: float) -> None:
    try:
        if process.poll() is None:
            if fsutil.IS_WINDOWS:
                fsutil._taskkill(process.pid, max(1.0, min(10.0, budget)))
            else:
                fsutil._kill_group(process.pid)
            try:
                process.wait(timeout=max(1.0, min(5.0, budget)))
            except subprocess.TimeoutExpired:
                process.kill()
        elif not fsutil.IS_WINDOWS:
            fsutil._kill_group(process.pid)
    except Exception:
        pass
    finally:
        fsutil.unregister_child(process)

def _run(node: str, harness: str, copy_dir: str, out_dir: str, env: dict, deadline: float):
    left = deadline - CLEANUP_RESERVE - time.time()
    if left < 3:
        return None, "", "not started: %.0fs left" % left
    os.makedirs(out_dir, exist_ok=True)
    log_path = out_dir + ".log"
    kwargs: dict = {}
    if fsutil.IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    with open(log_path, "wb") as sink:
        process = subprocess.Popen([node, harness, copy_dir, out_dir], cwd=copy_dir, env=env, stdout=sink,
                                   stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kwargs)
        fsutil.register_child(process, own_group=not fsutil.IS_WINDOWS)
        why = ""
        try:
            process.wait(timeout=left)
        except subprocess.TimeoutExpired:
            why = "timeout"
        finally:
            _stop(process, deadline - time.time())
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    return (process.returncode if not why else -1), text, why

def _snap(out_dir: str, name: str) -> str | None:
    p = os.path.join(out_dir, name)
    return p if os.path.isfile(p) else None

def _restart(project: Path, backend: Path, nm: str, node: str, deadline: float, requirement: str, log):
    if deadline - CLEANUP_RESERVE - time.time() < 6:
        log("[restart] not completed: no time left to start")
        return [], False
    wd = tempfile.mkdtemp(prefix="arc-restart-")
    try:
        harness = os.path.join(wd, "seed_harness.js")
        with open(harness, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(HARNESS_JS)
        copy_dir = os.path.join(wd, "backend")
        ignore = shutil.ignore_patterns("node_modules", "*.db", "*.db-wal", "*.db-shm", "*.db-journal", "*.sqlite",
                                        "*.sqlite3", ".arc-test-db", "test-results", "playwright-report", ".git")
        shutil.copytree(str(backend), copy_dir, ignore=ignore)
        if time.time() > deadline - CLEANUP_RESERVE:
            log("[restart] not completed: the deadline passed while copying the backend")
            return [], False
        data_dir = os.path.join(wd, "data")
        os.makedirs(data_dir)
        dbfile = os.path.join(data_dir, "database.db")
        env = dict(os.environ)
        env.update(NODE_PATH=nm, ARC_DB_FILE=dbfile, DATABASE_FILE=dbfile, DATA_DIR=data_dir, PORT="0",
                   NODE_ENV="test")
        t0 = time.time()

        out1, out2 = os.path.join(wd, "out1"), os.path.join(wd, "out2")
        rc1, text1, why1 = _run(node, harness, copy_dir, out1, env, deadline)
        a = _snap(out1, "lazy.db")
        if why1:
            log(f"[restart] not completed: first start {why1}")
            return [], False
        if rc1 != 0 or not a or not INIT_DONE.search(text1):
            log(f"[restart] not completed: first start did not finish (rc={rc1}, lazy snapshot="
                f"{'yes' if a else 'no'}) {text1[-300:]!r}")
            return [], False
        rc2, text2, why2 = _run(node, harness, copy_dir, out2, env, deadline)
        log(f"[restart] two starts on one database file: rc {rc1} -> {rc2}{' (' + why2 + ')' if why2 else ''}, "
            f"{time.time() - t0:.1f}s")
        if why2:
            log(f"[restart] not completed: second start {why2}")
            return [], False
        if rc2 != 0:
            return _second_start_failure(project, backend, copy_dir, node, harness, wd, env, deadline, text2, log)
        boot2, lazy2 = _snap(out2, "boot.db"), _snap(out2, "lazy.db")
        if not boot2:
            log("[restart] not completed: no snapshot after the second initialisation")
            return [], False

        names = seed_names(requirement)
        cmp_deadline = deadline - CLEANUP_RESERVE
        try:
            dups = compare_snapshots(a, boot2, cmp_deadline, names)
        except _Deadline:
            log("[restart] not completed: the deadline passed during the comparison")
            return [], False
        findings: list[dict[str, str]] = []
        for d in dups:
            if not d["seed_name"]:
                log(f"[restart]   (log only: no seed name of the requirement) {d['table']}: {d['count_a']} -> "
                    f"{d['count_b']} {d['sample'][:100]}")
                continue
            if len(findings) >= MAX_FINDINGS:
                break
            site = _insert_site(backend, project, d["table"])
            where = site[0] if site else _default_where(backend, project)
            ignored = "/".join(d["ignored"]) or "nothing"
            listed = ", ".join(f"`{x}`" for x in d["seed_names"][:4])
            problem = (f"Starting the server a second time on the same database file duplicates the seeded "
                       f"record{'s' if len(d['seed_names']) > 1 else ''} {listed}: table `{d['table']}` grew from {d['count_a']} to {d['count_b']} rows during "
                       f"the second start's initialisation; the record ({d['sample']}) was there {d['copies_a']} time(s) "
                       f"and is now there {d['copies_b']} times, equal except {ignored}"
                       + (f"; {d['records'] - 1} more record(s) likewise" if d["records"] > 1 else "")
                       + (f". Insert: {site[1]}" if site else "."))
            findings.append({"where": f"{CHECK}: {where}", "requirement": "", "problem": problem, "fix": FIX})
            log(f"[restart]   {d['table']}: {d['count_a']} -> {d['count_b']} `{d['seed_name']}` {d['sample'][:100]}")
        if lazy2:
            try:
                reported = {d["table"] for d in dups}
                for d in compare_snapshots(a, lazy2, cmp_deadline, names):
                    if d["table"] not in reported:
                        log(f"[restart]   (log only: after the ensure*/seed* helpers ran again) {d['table']}: "
                            f"{d['count_a']} -> {d['count_b']} {d['sample'][:100]}")
            except _Deadline:
                log("[restart] helper diagnostic skipped: deadline")
        if not findings:
            log(f"[restart] no duplicated seed rows after the restart ({len(names)} seed name(s) known)")
        return findings, True
    finally:
        shutil.rmtree(wd, ignore_errors=True)

def _second_start_failure(project, backend, copy_dir, node, harness, wd, env, deadline, text2, log):
    err = _failure_line(text2)
    if not err or INIT_DONE.search(text2) or TRANSIENT.search(err):
        log(f"[restart] second start failed but not certainly in the initialisation: {(err or text2[-200:])!r}")
        return [], False
    rc3, text3, why3 = _run(node, harness, copy_dir, os.path.join(wd, "out3"), env, deadline)
    if why3 or rc3 is None:
        log(f"[restart] not completed: confirmation start {why3}; error {err!r} unreported")
        return [], False
    if rc3 == 0 or _failure_line(text3) != err:
        log(f"[restart] second-start error not reproduced by a third start: {err!r}")
        return [], False
    site = _stack_site(text2, copy_dir, backend, project)
    if not site:
        m = re.search(r"(?i)constraint failed: ([A-Za-z_]\w*)\.", err) or \
            re.search(r"(?i)table [`\"']?([A-Za-z_]\w*)[`\"']? already exists", err)
        found = _insert_site(backend, project, m.group(1)) if m else None
        site = found[0] if found else _default_where(backend, project)
    log(f"[restart]   second start failed: {err}")
    problem = ("The first start on a fresh database file succeeds, but starting the server a second time on that same "
               f"file fails during the database initialisation (also on a third start): {err}")
    return [{"where": f"{CHECK}: {site}", "requirement": "", "problem": problem, "fix": FIX}], True

def restart_check(project_dir, requirement: str = "", time_limit: float = 60.0, log=print,
                  node_modules: str | None = None) -> tuple[list[dict[str, str]], bool]:
    deadline = time.time() + time_limit
    try:
        project = Path(project_dir).resolve()
        b = _backend_dir(str(project))
        if not b:
            log("[restart] skipped: no backend/src/database")
            return [], False
        backend = Path(b).resolve()
        nm = node_modules or str(backend / "node_modules")
        if not os.path.isdir(os.path.join(nm, "sqlite3")):
            log("[restart] skipped: node_modules/sqlite3 missing")
            return [], False
        node = shutil.which("node")
        if not node:
            log("[restart] skipped: no node on PATH")
            return [], False
        findings, completed = _restart(project, backend, nm, node, deadline, requirement or "", log)
        return findings[:MAX_FINDINGS], completed
    except Exception as exc:
        try:
            log(f"[restart] check failed: {exc!r}")
        except Exception:
            pass
        return [], False

def restart_findings(project_dir, time_limit: float = 60.0, log=print, node_modules: str | None = None,
                     requirement: str = "") -> list[dict[str, str]]:
    return restart_check(project_dir, requirement=requirement, time_limit=time_limit, log=log,
                         node_modules=node_modules)[0]
