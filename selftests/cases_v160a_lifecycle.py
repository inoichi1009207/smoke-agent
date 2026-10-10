from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import traceback
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import data_lifecycle as dl

NL = chr(10)
CASES = []
NODE_MODULES = os.environ.get("ARC_TX_NODE_MODULES", "local-data/try/sheet-v141/template/backend/node_modules")

def case(function):
    CASES.append(function)
    return function

def need_node() -> None:
    if not shutil.which("node") or not os.path.isdir(os.path.join(NODE_MODULES, "sqlite3")):
        raise unittest.SkipTest(f"node or {NODE_MODULES}/sqlite3 missing (set ARC_TX_NODE_MODULES)")

def make_db(path: str, statements: list[str]) -> str:
    con = sqlite3.connect(path)
    for s in statements:
        con.execute(s)
    con.commit()
    con.close()
    return path

@case
def names_in_db_exact_cells():
    with tempfile.TemporaryDirectory() as d:
        db = make_db(os.path.join(d, "a.db"), [
            "create table workbooks (id integer primary key, name text, note text)",
            "insert into workbooks (name, note) values ('  EVO-A ', 'about EVO-B here')",
            "insert into workbooks (name, note) values ('EVO-C', 'EVO-C')",
            "create table nums (id integer primary key, v)",
            "insert into nums (v) values (13)",
            "create table tags (k text primary key, label text) without rowid",
            "insert into tags values ('t1', 'EVO-D')",
            "create table logs (id integer primary key, message text)",
            "insert into logs (message) values ('EVO-A')",
        ])
        found = dl.names_in_db(db, ["EVO-A", " EVO-B", "EVO-C", "13", "EVO-D", "", None])
        assert set(found) == {"EVO-A", "EVO-B", "EVO-C", "13", "EVO-D"}, found
        assert found["EVO-A"] == [("logs", "message", 1), ("workbooks", "name", 1)], found
        assert found["EVO-B"] == [], found
        assert found["EVO-C"] == [("workbooks", "name", 2), ("workbooks", "note", 2)], found
        assert found["13"] == [], found
        assert found["EVO-D"] == [("tags", "label", None)], found
        assert dl.names_in_db(db, ["EVO-A"], skip_growing=True)["EVO-A"] == [("workbooks", "name", 1)]
        assert dl.names_in_db(db, []) == {}

@case
def corpus_presence_is_loose():
    with tempfile.TemporaryDirectory() as d:
        db = make_db(os.path.join(d, "a.db"), [
            "create table t (id integer primary key, v, j text)",
            "insert into t (v, j) values (13, '{\"rule\": \"Capacity must be from 25 to 75\"}')",
            "insert into t (v, j) values (2.5, 'evo-user')",
            "insert into t (v, j) values (7.0, null)",
        ])
        corpus = dl.db_corpus([db, None])
        for name in ("13", "2.5", "7", "Capacity must be from 25 to 75", "EVO-User", " evo-user "):
            assert dl.name_present(corpus, name), name
        for name in ("EVO-Z", "Ready"):
            assert not dl.name_present(corpus, name), name
        assert dl.name_present(None, "anything")

@case
def resurrected_rules():
    with tempfile.TemporaryDirectory() as d:
        sch = "create table w (id integer primary key, name text)"
        p = dl.PROBE_SUFFIX
        new = make_db(os.path.join(d, "new.db"), [sch, f"insert into w (name) values ('EVO-A{p}')",
                                                  "insert into w (name) values ('EVO-A')"])
        restored = make_db(os.path.join(d, "restored.db"), [sch, "insert into w (name) values ('EVO-A')"])
        stays = make_db(os.path.join(d, "stays.db"), [sch, f"insert into w (name) values ('EVO-A{p}')"])
        other = make_db(os.path.join(d, "other.db"), [sch, "create table x (id integer primary key, name text)",
                                                      f"insert into w (name) values ('EVO-A{p}')",
                                                      "insert into x (name) values ('EVO-A')"])
        assert dl.resurrected(new, "w", "name", "EVO-A", "rename") == "new"
        assert dl.resurrected(restored, "w", "name", "EVO-A", "rename") == "restored"
        assert dl.resurrected(stays, "w", "name", "EVO-A", "rename") == ""
        assert dl.resurrected(other, "w", "name", "EVO-A", "rename") == ""
        assert dl.resurrected(restored, "w", "name", "EVO-A", "delete") == "new"
        assert dl.resurrected(stays, "w", "name", "EVO-A", "delete") == ""

@case
def duplicate_seeds_reuses_compare_snapshots():
    with tempfile.TemporaryDirectory() as d:
        sch = "create table w (id integer primary key autoincrement, name text, created_at text)"
        a = make_db(os.path.join(d, "a.db"), [sch, "insert into w (name, created_at) values ('EVO-A', 't1')",
                                              "insert into w (name, created_at) values ('other', 't1')"])
        b = make_db(os.path.join(d, "b.db"), [sch, "insert into w (name, created_at) values ('EVO-A', 't1')",
                                              "insert into w (name, created_at) values ('other', 't1')",
                                              "insert into w (name, created_at) values ('EVO-A', 't2')",
                                              "insert into w (name, created_at) values ('other', 't2')"])
        named, unnamed = dl.duplicate_seeds(a, b, {"EVO-A"})
        assert len(named) == 1 and named[0]["table"] == "w" and named[0]["seed_names"] == ["EVO-A"], named
        assert unnamed == [], unnamed
        named, unnamed = dl.duplicate_seeds(a, b, set())
        assert named == [] and len(unnamed) == 1, (named, unnamed)

REQ = NL.join([
    "features:",
    "- name: workbook",
    "  scenarios:",
    "  - name: S1 - rename",
    "    steps:",
    "    - keyword: GIVEN",
    "      content: The visitor is unauthenticated and the system pre-provisions workbook `EVO-X` with active",
    "        worksheet `Sheet9` and cell F3 value `unreviewed`.",
    "    - keyword: WHEN",
    "      content: The visitor opens `EVO-X` and types `zzz`.",
    "  - name: S2 - search",
    "    steps:",
    "    - keyword: GIVEN",
    "      content: The system pre-provisions the search repositories and none contains the query `evo-empty`.",
    "  - name: S3 - register",
    "    steps:",
    "    - keyword: GIVEN",
    "      content: The system has pre-provisioned the verified and available account `evo-user` with password",
    "        `Pw-1!`; `evo.s3@x.test` is unused.",
    "  - name: S4 - plain",
    "    steps:",
    "    - keyword: GIVEN",
    "      content: A workbook `Not-Preprovisioned` is open.",
    "",
])

@case
def scenario_names_and_vacancies():
    scen = dl.scenario_seed_names(REQ)
    assert scen == {"S1 - rename": ["EVO-X", "Sheet9", "unreviewed"], "S2 - search": ["evo-empty"],
                    "S3 - register": ["evo-user", "Pw-1!", "evo.s3@x.test"]}, scen
    names = [n for v in scen.values() for n in v]
    assert dl.vacancy_names(REQ, names) == {"evo-empty", "evo.s3@x.test"}, dl.vacancy_names(REQ, names)
    assert dl.scenario_seed_names("") == {}
    plain = "Given the system pre-provisions workbook `EVO-P`." + NL + "When the user opens `EVO-P`."
    assert dl.scenario_seed_names(plain) == {"line 1": ["EVO-P"]}

@case
def probe_plan_rules():
    with tempfile.TemporaryDirectory() as d:
        db = make_db(os.path.join(d, "a.db"), [
            "create table workbooks (id integer primary key, name text)",
            "create table users (id integer primary key, username text)",
            "create table sessions (id integer primary key, label text)",
            "insert into workbooks (name) values ('W1')", "insert into workbooks (name) values ('W2')",
            "insert into workbooks (name) values ('W3')", "insert into workbooks (name) values ('DUP')",
            "insert into workbooks (name) values ('DUP')",
            "insert into users (username) values ('U1')",
            "insert into sessions (label) values ('S1')",
        ])
        prim = {"a": "W1", "b": "W2", "c": "W3", "d": "U1", "e": "DUP", "f": "S1", "g": "ABSENT"}
        plan = dl._plan_probes(db, {}, prim)
        names = [(k, n) for k, n, *_ in plan]

        assert names == [("rename", "W1"), ("delete", "U1"), ("rename", "W2"), ("delete", "W3")], names
        assert all(t != "sessions" for _, _, _, t, _, _ in plan), plan
        single = dl._plan_probes(db, {}, {"a": "W1"})
        assert [(k, n) for k, n, *_ in single] == [("rename", "W1"), ("delete", "W1")], single
        assert dl._plan_probes(db, {}, {"x": "DUP"}) == []

@case
def never_raises_and_skips_with_a_note():
    lines = []
    f, r = dl.lifecycle_check(Path("Z:/no/such/project"), log=lines.append)
    assert f == [] and r["completed"] is False and any("skipped" in n for n in r["notes"]), r
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "backend" / "src" / "database").mkdir(parents=True)
        (Path(d) / "backend" / "src" / "database" / "init_db.js").write_text("module.exports = {};", encoding="utf-8")
        f, r = dl.lifecycle_check(Path(d), log=lines.append, node_modules=os.path.join(d, "nm"))
        assert f == [] and not r["completed"] and any("sqlite3" in n for n in r["notes"]), r
        assert set(r["paths"]) == {"empty", "restart", "upgrade", "resurrection"} and r["paths"]["upgrade"] == "skipped"
    f, r = dl.lifecycle_check(None, log=lines.append, scenario_names={"s": None, "t": [1, " "]})
    assert f == [] and r["completed"] is False and r["notes"], r

PRELUDE = """'use strict';
const sqlite3 = require('sqlite3');
const file = process.env.ARC_DB_FILE;
let db = null;
function getDb() { if (!db) db = new sqlite3.Database(file); return db; }
function run(sql, p = []) { return new Promise((res, rej) => getDb().run(sql, p, (e) => (e ? rej(e) : res()))); }
function get(sql, p = []) { return new Promise((res, rej) => getDb().get(sql, p, (e, r) => (e ? rej(e) : res(r)))); }
function all(sql, p = []) { return new Promise((res, rej) => getDb().all(sql, p, (e, r) => (e ? rej(e) : res(r)))); }
function closeDb() { return new Promise((res) => { if (!db) { res(); return; } const d = db; db = null; d.close(() => res()); }); }
async function seedOnce(key, insert) {
  await run('CREATE TABLE IF NOT EXISTS seed_markers (key TEXT PRIMARY KEY)');
  if (await get('SELECT 1 AS x FROM seed_markers WHERE key = ?', [key])) return;
  await run('BEGIN');
  try { await insert(); await run('INSERT INTO seed_markers (key) VALUES (?)', [key]); await run('COMMIT'); }
  catch (e) { await run('ROLLBACK'); throw e; }
}
"""
EXPORTS = NL + "module.exports = { initializeDatabase, getDb, closeDb, run, get, all };" + NL

def write_backend(backend: Path, body: str, extra: dict[str, str] | None = None) -> None:
    dbdir = backend / "src" / "database"
    dbdir.mkdir(parents=True)
    (backend / "package.json").write_text('{"name": "backend", "version": "1.0.0"}', encoding="utf-8")
    (dbdir / "init_db.js").write_text(PRELUDE + "async function initializeDatabase() {" + NL + body + NL + "}" + EXPORTS,
                                      encoding="utf-8", newline=NL)
    for rel, text in (extra or {}).items():
        path = backend / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline=NL)

def fixture(body: str, extra: dict[str, str] | None = None) -> Path:
    proj = Path(tempfile.mkdtemp(prefix="v160a-fixture-"))
    write_backend(proj / "backend", body, extra)
    return proj

def listing(p: Path) -> list[str]:
    return sorted(str(x.relative_to(p)) for x in p.rglob("*"))

def run_check(proj: Path, scenario_names=None, requirement: str = "", old_body: str | None = None,
              time_limit: float = 150):
    lines: list[str] = []
    old = None
    try:
        if old_body is not None:
            old = Path(tempfile.mkdtemp(prefix="v160a-old-"))
            write_backend(old / "backend", old_body)
        before = listing(proj)
        f, r = dl.lifecycle_check(proj, requirement, scenario_names=scenario_names,
                                  inherited_backend=(old / "backend") if old else None, time_limit=time_limit,
                                  log=lines.append, node_modules=NODE_MODULES)
        assert listing(proj) == before, "the check wrote inside project_dir"
        for item in f:
            assert set(item) == {"where", "requirement", "problem", "fix", "check"}, item
            assert item["where"].startswith("data-lifecycle: ") and item["check"].startswith("data-lifecycle/"), item
        return f, r, lines
    finally:
        shutil.rmtree(proj, ignore_errors=True)
        if old:
            shutil.rmtree(old, ignore_errors=True)

def kinds(findings) -> list[str]:
    return [f["check"].split("/", 1)[1] for f in findings]

SCEN_AB = {"S1": ["EVO-A"], "S2": ["EVO-B"]}
NAME_KEYED = """
  await run('CREATE TABLE IF NOT EXISTS workbooks (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL)');
  for (const n of ['EVO-A', 'EVO-B']) {
    const found = await get('SELECT id FROM workbooks WHERE name = ?', [n]);
    if (!found) await run('INSERT INTO workbooks (name) VALUES (?)', [n]);
  }
"""
MARKER_KEYED = """
  await run('CREATE TABLE IF NOT EXISTS workbooks (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL)');
  for (const n of ['EVO-A', 'EVO-B']) {
    await seedOnce('workbook:' + n, () => run('INSERT INTO workbooks (name) VALUES (?)', [n]));
  }
"""
OLD_V1 = """
  await run('CREATE TABLE IF NOT EXISTS workbooks (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL)');
  await seedOnce('workbook:Q3 Sales', () => run('INSERT INTO workbooks (name) VALUES (?)', ['Q3 Sales']));
"""
CUR_NO_MIGRATION = """
  await run("CREATE TABLE IF NOT EXISTS workbooks (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, color TEXT NOT NULL DEFAULT 'none')");
  await seedOnce('workbook:Q3 Sales', () => run('INSERT INTO workbooks (name) VALUES (?)', ['Q3 Sales']));
  await seedOnce('workbook:EVO-A', () => run('INSERT INTO workbooks (name, color) VALUES (?, ?)', ['EVO-A', 'red']));
"""
CUR_MIGRATION = CUR_NO_MIGRATION.replace("""  await seedOnce('workbook:Q3 Sales'""", """  const cols = await all('PRAGMA table_info(workbooks)');
  if (!cols.some((c) => c.name === 'color')) {
    await run('BEGIN');
    await run("ALTER TABLE workbooks ADD COLUMN color TEXT NOT NULL DEFAULT 'none'");
    await run("UPDATE workbooks SET color = 'none' WHERE color IS NULL");
    await run('COMMIT');
  }
  await seedOnce('workbook:Q3 Sales'""", 1)
OLD_FRESH_ONLY = """
  const existed = await get("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'workbooks'");
  await run('CREATE TABLE IF NOT EXISTS workbooks (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL)');
  if (!existed) await run('INSERT INTO workbooks (name) VALUES (?)', ['Q3 Sales']);
"""
CUR_FRESH_ONLY = OLD_FRESH_ONLY.replace(
    "  if (!existed) await run('INSERT INTO workbooks (name) VALUES (?)', ['Q3 Sales']);",
    "  if (!existed) {" + NL + "    for (const n of ['Q3 Sales', 'EVO-A']) await run('INSERT INTO workbooks (name) VALUES (?)', [n]);"
    + NL + "  }")
OR_IGNORE_NO_UNIQUE = """
  await run('CREATE TABLE IF NOT EXISTS workbooks (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL)');
  await run('INSERT OR IGNORE INTO workbooks (name) VALUES (?)', ['EVO-A']);
"""
INDEX_BEFORE_TABLE = """
  await run('CREATE INDEX IF NOT EXISTS idx_sheets_name ON sheets (name)');
  await run('CREATE TABLE IF NOT EXISTS sheets (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)');
"""
ALWAYS_LOCKED = """
  throw new Error('SQLITE_BUSY: database is locked');
"""
SECOND_START_THROWS = """
  await run('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE)');
  await run('INSERT INTO users (username) VALUES (?)', ['EVO-A']);
"""
SEEDS_Q3_ONLY = """
  await run('CREATE TABLE IF NOT EXISTS workbooks (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL)');
  await seedOnce('workbook:Q3 Sales', () => run('INSERT INTO workbooks (name) VALUES (?)', ['Q3 Sales']));
"""
REQ_MISSING = NL.join([
    "features:",
    "- name: workbook",
    "  scenarios:",
    "  - name: S1",
    "    steps:",
    "    - keyword: GIVEN",
    "      content: The system pre-provisions workbook `EVO-X` with active worksheet `Sheet9`.",
    "  - name: S2",
    "    steps:",
    "    - keyword: GIVEN",
    "      content: The system pre-provisions the search repositories and none contains the query `evo-empty`.",
    "  - name: S3",
    "    steps:",
    "    - keyword: GIVEN",
    "      content: The system pre-provisions workbook `EVO-SRC`.",
    "  - name: S4",
    "    steps:",
    "    - keyword: GIVEN",
    "      content: The system pre-provisions workbook `Q3 Sales` with cell A1 value `1200`.",
    "",
])

@case
def name_keyed_seed_resurrects():
    need_node()
    f, r, lines = run_check(fixture(NAME_KEYED), SCEN_AB)
    assert sorted(kinds(f)) == ["deleted_resurrects", "renamed_resurrects"], (f, lines)
    ren = next(x for x in f if x["check"].endswith("renamed_resurrects"))
    assert "`workbooks`" in ren["problem"] and "`EVO-A`" in ren["problem"] and ren["requirement"] == "S1", ren
    assert ren["where"].startswith("data-lifecycle: backend/src/database/init_db.js:"), ren
    line = int(ren["where"].rsplit(":", 1)[1])
    src = (PRELUDE + "async function initializeDatabase() {" + NL + NAME_KEYED).split(NL)
    assert "EVO-A" in src[line - 1], (line, ren)
    assert "seed_markers" in ren["fix"] and "display name" in ren["fix"], ren
    dele = next(x for x in f if x["check"].endswith("deleted_resurrects"))
    assert "`EVO-B`" in dele["problem"] and dele["requirement"] == "S2", dele
    assert r["paths"] == {"empty": "ok", "restart": "ok", "upgrade": "skipped", "resurrection": "failed"}, r
    assert r["completed"] is True, r

@case
def marker_keyed_seed_is_clean():
    need_node()
    tmp = Path(tempfile.gettempdir())
    before = {p.name for p in tmp.glob("arc-lifecycle-*")}
    f, r, lines = run_check(fixture(MARKER_KEYED), SCEN_AB)
    assert f == [], (f, lines)
    assert r["paths"] == {"empty": "ok", "restart": "ok", "upgrade": "skipped", "resurrection": "ok"}, (r, lines)
    assert r["completed"] and r["missing"] == {}, r
    assert {p.name for p in tmp.glob("arc-lifecycle-*")} == before, "temp dir left behind"

@case
def upgrade_without_migration_fails_with_migration_is_clean():
    need_node()
    f, r, lines = run_check(fixture(CUR_NO_MIGRATION), {"S1": ["EVO-A"]}, old_body=OLD_V1)
    assert kinds(f) == ["upgrade_failed"], (f, lines)
    assert "no column named color" in f[0]["problem"] and "ALTER TABLE" in f[0]["fix"], f
    assert "PRAGMA table_info" in f[0]["fix"], f
    assert r["paths"]["upgrade"] == "failed" and r["paths"]["empty"] == "ok", r
    f, r, lines = run_check(fixture(CUR_MIGRATION), {"S1": ["EVO-A"]}, old_body=OLD_V1)
    assert f == [], (f, lines)
    assert r["paths"] == {"empty": "ok", "restart": "ok", "upgrade": "ok", "resurrection": "ok"}, (r, lines)
    assert r["completed"], r

@case
def seed_only_in_new_tables_misses_on_upgrade():
    need_node()
    f, r, lines = run_check(fixture(CUR_FRESH_ONLY), {"S1": ["EVO-A"]}, old_body=OLD_FRESH_ONLY)
    assert kinds(f) == ["upgrade_missing_seed"], (f, lines)
    assert "`EVO-A`" in f[0]["problem"] and f[0]["requirement"] == "S1", f
    assert r["paths"] == {"empty": "ok", "restart": "ok", "upgrade": "failed", "resurrection": "ok"}, (r, lines)

@case
def restart_duplicates_reported_once():
    need_node()
    f, r, lines = run_check(fixture(OR_IGNORE_NO_UNIQUE), {"S1": ["EVO-A"]})
    assert kinds(f) == ["restart_duplicates"], (f, lines)
    assert "`workbooks` grew from 1 to 2" in f[0]["problem"] and "`EVO-A`" in f[0]["problem"], f
    assert r["paths"]["restart"] == "failed", r
    assert any("plain restart already duplicates" in n for n in r["notes"]), r

@case
def empty_start_failure_confirmed_transient_not():
    need_node()
    f, r, lines = run_check(fixture(INDEX_BEFORE_TABLE), {"S1": ["EVO-A"]})
    assert kinds(f) == ["empty_start_failed"], (f, lines)
    assert "no such table" in f[0]["problem"] and "EMPTY database" in f[0]["problem"], f
    assert r["paths"]["empty"] == "failed" and not r["completed"], r
    f, r, lines = run_check(fixture(ALWAYS_LOCKED), {"S1": ["EVO-A"]})
    assert f == [] and r["paths"]["empty"] == "unknown" and not r["completed"], (f, r, lines)

@case
def restart_failure_confirmed():
    need_node()
    f, r, lines = run_check(fixture(SECOND_START_THROWS), {"S1": ["EVO-A"]})
    assert kinds(f) == ["restart_failed"], (f, lines)
    assert "UNIQUE constraint failed: users.username" in f[0]["problem"] and "start #2" in f[0]["problem"], f
    assert r["paths"]["restart"] == "failed" and r["paths"]["resurrection"] == "unknown", r

@case
def missing_scenario_seed_with_vacancy_and_source_guards():
    need_node()
    extra = {"src/routes/later.js": "module.exports = { planned: 'EVO-SRC' };" + NL}
    f, r, lines = run_check(fixture(SEEDS_Q3_ONLY, extra), None, requirement=REQ_MISSING)
    assert kinds(f) == ["scenario_seed_missing"], (f, lines)
    assert "`EVO-X`" in f[0]["problem"] and "1 record(s)" in f[0]["problem"], f
    assert "evo-empty" not in f[0]["problem"] and "EVO-SRC" not in f[0]["problem"], f
    assert f[0]["requirement"] == "S1", f
    assert r["missing"] == {"S1": ["EVO-X"]}, r
    assert r["missing_secondary"] == {"S1": ["Sheet9"], "S4": ["1200"]}, r
    assert any("EVO-SRC" in n and "later.js" in n for n in r["notes"]), r
    assert r["paths"]["empty"] == "failed", r

def main() -> int:
    if not __debug__:
        print("FAIL  self-tests need assertions: run without -O and without PYTHONOPTIMIZE")
        return 1
    failed = skipped = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except unittest.SkipTest as why:
            skipped += 1
            print(f"SKIP  {function.__name__}: {why}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=4)
    print(f"{len(CASES) - failed - skipped}/{len(CASES)} self-tests passed, {skipped} skipped")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
