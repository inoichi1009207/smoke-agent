from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import seed_restart as sr

SERIAL = True
NL = chr(10)
CASES = []
NODE_MODULES = os.environ.get("ARC_TX_NODE_MODULES", "local-data/try/sheet-v141/template/backend/node_modules")
REQ_Q3 = NL.join([
    "features:",
    "- name: workbook",
    "  scenarios:",
    "  - name: REQ-1-1-1",
    "    steps:",
    "    - keyword: GIVEN",
    "      content: The evaluation seed contains the seeded workbook `Q3 Sales` and worksheet `Sheet1`.",
    "    - keyword: WHEN",
    "      content: The user types `East` into A2.",
    "",
])

def case(function):
    CASES.append(function)
    return function

def have_node() -> bool:
    if not shutil.which("node") or not os.path.isdir(os.path.join(NODE_MODULES, "sqlite3")):
        print(f"note  node or {NODE_MODULES}/sqlite3 missing: node fixture skipped (set ARC_TX_NODE_MODULES)")
        return False
    return True

def make_db(path: str, statements: list[str]) -> str:
    con = sqlite3.connect(path)
    for s in statements:
        con.execute(s)
    con.commit()
    con.close()
    return path

@case
def compare_snapshots_rules():
    with tempfile.TemporaryDirectory() as d:
        wb = "create table workbooks (id integer primary key autoincrement, name text not null, last_updated text)"
        tags = "create table tags (id integer primary key, name text)"
        stable = "create table stable (id integer primary key, name text)"
        ins_wb = "insert into workbooks (name, last_updated) values ('Q3 Sales', '{}')"
        a = make_db(os.path.join(d, "a.db"), [wb, tags, stable, ins_wb.format("t1"),
                                               "insert into tags (name) values ('x')", "insert into tags (name) values ('x')",
                                               "insert into stable (name) values ('s')", "insert into stable (name) values ('s')"])
        b = make_db(os.path.join(d, "b.db"), [wb, tags, stable, ins_wb.format("t1"), ins_wb.format("t2"),
                                               "insert into tags (name) values ('x')", "insert into tags (name) values ('x')",
                                               "insert into tags (name) values ('x')",
                                               "insert into stable (name) values ('s')", "insert into stable (name) values ('s')"])
        res = {r["table"]: r for r in sr.compare_snapshots(a, b, names={"Q3 Sales"})}
        assert set(res) == {"workbooks", "tags"}, res
        w = res["workbooks"]
        assert (w["count_a"], w["count_b"], w["copies_a"], w["copies_b"]) == (1, 2, 1, 2), w
        assert w["seed_name"] == "Q3 Sales" and w["seed_names"] == ["Q3 Sales"], w
        assert set(w["ignored"]) == {"id", "last_updated"}, w
        t = res["tags"]
        assert (t["copies_a"], t["copies_b"], t["seed_name"]) == (2, 3, None), t

        for kind, ids, expect in (("uuid", ("5f0c7a52-9a43-4c1e-8d43-1b2a3c4d5e6f", "0a1b2c3d-1111-4222-8333-444455556666"), 1),
                                  ("slug", ("q3-sales", "q3-sales-2"), 0)):
            sch = "create table books (id text primary key, name text)"
            a2 = make_db(os.path.join(d, f"a_{kind}.db"), [sch, f"insert into books values ('{ids[0]}', 'Q3 Sales')"])
            b2 = make_db(os.path.join(d, f"b_{kind}.db"), [sch, f"insert into books values ('{ids[0]}', 'Q3 Sales')",
                                                           f"insert into books values ('{ids[1]}', 'Q3 Sales')"])
            assert len(sr.compare_snapshots(a2, b2, names={"Q3 Sales"})) == expect, kind

        c = make_db(os.path.join(d, "c.db"), [wb.replace("last_updated text", "last_updated text, x int"),
                                               ins_wb.format("t1"), ins_wb.format("t2")])
        a3 = make_db(os.path.join(d, "a3.db"), [wb, ins_wb.format("t1")])
        assert sr.compare_snapshots(a3, c) == []

        try:
            sr.compare_snapshots(a, b, deadline=time.time() - 1)
            raise AssertionError("no deadline stop")
        except sr._Deadline:
            pass

@case
def seed_names_sources():
    names = sr.seed_names(REQ_Q3)
    assert {"Q3 Sales", "Sheet1"} <= names, names
    assert "East" not in names, names
    text = NL.join(["Seed data: repository `acme-docs` owned by `alice-dev`.",
                    "Given a label `bug` exists", "When the user clicks `Save`.",
                    "The seeded workbook is `Budget`. The user then types `zzz`."])
    names = sr.seed_names(text)
    assert {"acme-docs", "alice-dev", "bug", "Budget"} <= names and not {"Save", "zzz"} & names, names
    assert sr.seed_names("") == set()

@case
def never_raises_and_skips_with_a_log_line():
    lines = []
    assert sr.restart_check(Path("Z:/no/such/project"), log=lines.append) == ([], False)
    assert lines and lines[0].startswith("[restart] skipped"), lines
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "backend" / "src" / "database").mkdir(parents=True)
        (Path(d) / "backend" / "src" / "database" / "init_db.js").write_text("module.exports = {};", encoding="utf-8")
        lines = []
        assert sr.restart_check(Path(d), log=lines.append, node_modules=os.path.join(d, "nm")) == ([], False)
        assert any("sqlite3 missing" in ln for ln in lines), lines
        if have_node():
            lines = []
            t = time.time()
            assert sr.restart_check(Path(d), time_limit=2, log=lines.append, node_modules=NODE_MODULES) == ([], False)
            assert time.time() - t < 2 and any("no time left" in ln for ln in lines), lines
    lines = []
    assert sr.restart_check(None, log=lines.append) == ([], False)
    assert lines, "a crash must be logged"
    assert sr.restart_findings(None, log=lambda _m: None) == []

PRELUDE = """'use strict';
const sqlite3 = require('sqlite3');
const file = process.env.ARC_DB_FILE;
let db = null;
function getDb() { if (!db) db = new sqlite3.Database(file); return db; }
function run(sql, p = []) { return new Promise((res, rej) => getDb().run(sql, p, (e) => (e ? rej(e) : res()))); }
function get(sql, p = []) { return new Promise((res, rej) => getDb().get(sql, p, (e, r) => (e ? rej(e) : res(r)))); }
function closeDb() { return new Promise((res) => { if (!db) { res(); return; } const d = db; db = null; d.close(() => res()); }); }
"""
EXPORTS = NL + "module.exports = { initializeDatabase, getDb, closeDb, run, get };" + NL

def fixture(body: str, extra: dict[str, str] | None = None) -> Path:
    proj = Path(tempfile.mkdtemp(prefix="v149b-fixture-"))
    dbdir = proj / "backend" / "src" / "database"
    dbdir.mkdir(parents=True)
    (dbdir / "init_db.js").write_text(PRELUDE + "async function initializeDatabase() {" + NL + body + NL + "}" + EXPORTS,
                                      encoding="utf-8", newline=NL)
    for name, text in (extra or {}).items():
        (dbdir / name).write_text(text, encoding="utf-8", newline=NL)
    return proj

def listing(p: Path) -> list[str]:
    return sorted(str(x.relative_to(p)) for x in p.rglob("*"))

def run_check(proj: Path, requirement: str = REQ_Q3, time_limit: float = 60):
    lines: list[str] = []
    try:
        before = listing(proj)
        res, done = sr.restart_check(proj, requirement=requirement, time_limit=time_limit, log=lines.append,
                                     node_modules=NODE_MODULES)
        assert listing(proj) == before, "the check wrote inside project_dir"
        return res, done, lines
    finally:
        shutil.rmtree(proj, ignore_errors=True)

IDEMPOTENT = """
  await run('CREATE TABLE IF NOT EXISTS workbooks (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, created_at TEXT)');
  const found = await get('SELECT id FROM workbooks WHERE name = ?', ['Q3 Sales']);
  if (!found) await run('INSERT INTO workbooks (name, created_at) VALUES (?, ?)', ['Q3 Sales', new Date().toISOString()]);
"""
OR_IGNORE_NO_UNIQUE = """
  await run('CREATE TABLE IF NOT EXISTS workbooks (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, created_at TEXT)');
  await run('INSERT OR IGNORE INTO workbooks (name, created_at) VALUES (?, ?)', ['Q3 Sales', new Date().toISOString()]);
"""
OR_IGNORE_UNIQUE = OR_IGNORE_NO_UNIQUE.replace("name TEXT NOT NULL,", "name TEXT NOT NULL UNIQUE,")
GROWING = """
  await run('CREATE TABLE IF NOT EXISTS pings (id INTEGER PRIMARY KEY AUTOINCREMENT, nonce TEXT NOT NULL)');
  await run('INSERT INTO pings (nonce) VALUES (?)', [String(Math.random()) + String(Date.now())]);
  await run('CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, message TEXT NOT NULL)');
  await run('INSERT INTO logs (message) VALUES (?)', ['Q3 Sales']);
  await run('CREATE TABLE IF NOT EXISTS counters (id INTEGER PRIMARY KEY AUTOINCREMENT, n INTEGER NOT NULL)');
  await run('INSERT INTO counters (n) VALUES (1)');
  await run('CREATE TABLE IF NOT EXISTS measurements (id INTEGER PRIMARY KEY, sensor TEXT, value REAL, timestamp TEXT)');
  await run('INSERT INTO measurements (sensor, value, timestamp) VALUES (?, ?, ?)', ['temp', 21.5, new Date().toISOString()]);
"""
UUID_PK = """
  await run('CREATE TABLE IF NOT EXISTS workbooks (id TEXT PRIMARY KEY, name TEXT NOT NULL)');
  await run('INSERT OR IGNORE INTO workbooks (id, name) VALUES (?, ?)', [require('crypto').randomUUID(), 'Q3 Sales']);
"""
TWICE_PER_START = """
  await run('CREATE TABLE IF NOT EXISTS sheets (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL)');
  await run('INSERT INTO sheets (name) VALUES (?)', ['Sheet1']);
  await run('INSERT INTO sheets (name) VALUES (?)', ['Sheet1']);
"""
SECOND_START_THROWS = """
  await run('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE)');
  await run('INSERT INTO users (username) VALUES (?)', ['alice']);
"""
SECOND_START_LOCKED = """
  await run('CREATE TABLE IF NOT EXISTS marks (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)');
  const seen = await get('SELECT id FROM marks WHERE name = ?', ['m']);
  if (seen) throw new Error('SQLITE_BUSY: database is locked');
  await run('INSERT INTO marks (name) VALUES (?)', ['m']);
"""
FIRST_START_FAILS = """
  throw new Error('boom on every start');
"""
HELPER_TABLE = """
  await run('CREATE TABLE IF NOT EXISTS tags (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL)');
"""
HELPER_SEED = """'use strict';
const { run } = require('./init_db');
async function seedExamples() { await run('INSERT INTO tags (name) VALUES (?)', ['Q3 Sales']); }
module.exports = { seedExamples };
"""
DATA_STORE = """
  const fs = require('fs'); const path = require('path');
  const dir = process.env.DATA_DIR;
  if (!dir || !/arc-restart-/.test(dir)) throw new Error('DATA_DIR not isolated: ' + dir);
  fs.mkdirSync(dir, { recursive: true });
  fs.appendFileSync(path.join(dir, 'store.json'), 'x');
  await run('CREATE TABLE IF NOT EXISTS workbooks (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE)');
  await run('INSERT OR IGNORE INTO workbooks (name) VALUES (?)', ['Q3 Sales']);
"""

HANG_WITH_CHILD = """
  const cp = require('child_process');
  const child = cp.spawn(process.execPath, ['-e', 'setTimeout(() => {}, 120000)'], { stdio: 'ignore', detached: true });
  require('fs').writeFileSync(process.env.ARC_V149B_PIDFILE, String(child.pid));
  await new Promise(() => {});
"""

@case
def idempotent_seed_no_finding():
    if not have_node():
        return
    res, done, lines = run_check(fixture(IDEMPOTENT))
    assert res == [] and done, (res, lines)

@case
def or_ignore_without_unique_reported_only_with_a_seed_name():
    if not have_node():
        return
    tmp = Path(tempfile.gettempdir())
    before = {p.name for p in tmp.glob("arc-restart-*")}
    res, done, lines = run_check(fixture(OR_IGNORE_NO_UNIQUE))
    assert {p.name for p in tmp.glob("arc-restart-*")} == before, "temp dir left behind"
    assert done and len(res) == 1, (res, lines)
    f = res[0]
    assert set(f) == {"where", "requirement", "problem", "fix"}
    assert f["where"].startswith("restart: backend/src/database/init_db.js:"), f
    line = int(f["where"].rsplit(":", 1)[1])
    src = PRELUDE + "async function initializeDatabase() {" + NL + OR_IGNORE_NO_UNIQUE
    assert "INSERT OR IGNORE INTO workbooks" in src.split(NL)[line - 1], (line, f)
    assert "`workbooks` grew from 1 to 2" in f["problem"] and "`Q3 Sales`" in f["problem"], f
    assert "second start's initialisation" in f["problem"] and "UNIQUE" in f["fix"], f
    res, done, lines = run_check(fixture(OR_IGNORE_NO_UNIQUE), requirement="A spreadsheet app.")
    assert res == [] and done, res
    assert any("log only: no seed name" in ln and "workbooks" in ln for ln in lines), lines

@case
def unique_plus_or_ignore_no_finding():
    if not have_node():
        return
    res, done, lines = run_check(fixture(OR_IGNORE_UNIQUE))
    assert res == [] and done, (res, lines)

@case
def tables_growing_by_design_not_reported():
    if not have_node():
        return
    res, done, lines = run_check(fixture(GROWING))
    assert res == [] and done, (res, lines)
    assert any("measurements" in ln and "log only" in ln for ln in lines), lines

@case
def uuid_key_and_twice_per_start_reported():
    if not have_node():
        return
    res, done, lines = run_check(fixture(UUID_PK))
    assert done and len(res) == 1 and "`workbooks` grew from 1 to 2" in res[0]["problem"], (res, lines)
    res, done, lines = run_check(fixture(TWICE_PER_START))
    assert done and len(res) == 1, (res, lines)
    assert "`sheets` grew from 2 to 4" in res[0]["problem"] and "there 2 time(s)" in res[0]["problem"], res

@case
def second_start_failure_reported_transient_and_first_failure_not():
    if not have_node():
        return
    res, done, lines = run_check(fixture(SECOND_START_THROWS))
    assert done and len(res) == 1, (res, lines)
    f = res[0]
    assert "UNIQUE constraint failed: users.username" in f["problem"], f
    assert "second time" in f["problem"] and f["where"].startswith("restart: backend/src/database/init_db.js:"), f
    res, done, lines = run_check(fixture(SECOND_START_LOCKED))
    assert res == [] and not done, (res, lines)
    assert any("not certainly" in ln for ln in lines), lines
    res, done, lines = run_check(fixture(FIRST_START_FAILS))
    assert res == [] and not done, (res, lines)
    assert any("first start did not finish" in ln for ln in lines), lines

@case
def helper_only_growth_is_log_only():
    if not have_node():
        return
    res, done, lines = run_check(fixture(HELPER_TABLE, {"seed_examples.js": HELPER_SEED}))
    assert res == [] and done, (res, lines)
    assert any("after the ensure*/seed* helpers ran again" in ln and "tags" in ln for ln in lines), lines

@case
def inherited_data_dir_is_replaced():
    if not have_node():
        return
    sentinel = Path(tempfile.mkdtemp(prefix="v149b-inherited-data-"))
    saved = os.environ.get("DATA_DIR")
    os.environ["DATA_DIR"] = str(sentinel)
    try:
        res, done, lines = run_check(fixture(DATA_STORE))
        assert res == [] and done, (res, lines)
        assert list(sentinel.iterdir()) == [], "the app wrote into the inherited DATA_DIR"
    finally:
        if saved is None:
            os.environ.pop("DATA_DIR", None)
        else:
            os.environ["DATA_DIR"] = saved
        shutil.rmtree(sentinel, ignore_errors=True)

def pid_alive(pid: int) -> bool:
    if os.name == "nt":

        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

@case
def hang_returns_in_budget_and_kills_the_group():
    if not have_node():
        return
    pidfile = Path(tempfile.mkdtemp(prefix="v149b-pid-")) / "child.pid"
    os.environ["ARC_V149B_PIDFILE"] = str(pidfile)
    try:
        t = time.time()
        res, done, lines = run_check(fixture(HANG_WITH_CHILD), time_limit=12)
        took = time.time() - t
        assert res == [] and not done, (res, lines)
        assert took < 12 + 5, f"took {took:.1f}s"
        assert any("not completed" in ln for ln in lines), lines
        assert pidfile.is_file(), "the fixture never started its child"
        pid = int(pidfile.read_text())
        for _ in range(20):
            if not pid_alive(pid):
                break
            time.sleep(0.25)
        assert not pid_alive(pid), f"child {pid} survived"
    finally:
        os.environ.pop("ARC_V149B_PIDFILE", None)
        shutil.rmtree(pidfile.parent, ignore_errors=True)

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=4)
    print(f"{len(CASES) - failed}/{len(CASES)} self-tests passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
