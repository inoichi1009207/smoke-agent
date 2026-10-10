from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import traceback
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("V182_UNDO_ROOT") or HERE.parent)
sys.path.insert(0, str(ROOT))

from agent import data_lifecycle as dl

V181 = HERE.parent.parent / "smoke-agent-v181"
KEY = "lifecycle_report_rename_undo"
CASES: list = []

def case(function):
    CASES.append(function)
    return function

class Switch:

    def __init__(self, **values):
        self.values, self.saved = values, {}

    def __enter__(self):
        from agent.settings import SETTINGS

        for key, value in self.values.items():
            self.saved[key] = (key in SETTINGS, SETTINGS.get(key))
            SETTINGS[key] = value
        return self

    def __exit__(self, *exc):
        from agent.settings import SETTINGS

        for key, (had, value) in self.saved.items():
            if had:
                SETTINGS[key] = value
            else:
                SETTINGS.pop(key, None)
        return False

SFX = dl.PROBE_SUFFIX

def _revert_same_row(con):
    con.execute("UPDATE workbooks SET name = replace(name, ?, '') WHERE name LIKE ?", [SFX, "%" + SFX])

def _revert_new_row(con):
    for (name,) in con.execute("SELECT name FROM workbooks WHERE name LIKE ?", ["%" + SFX]).fetchall():
        con.execute("DELETE FROM workbooks WHERE name = ?", [name])
        con.execute("INSERT INTO workbooks (name) VALUES (?)", [name[: -len(SFX)]])

def _revert_and_copy(con):
    rows = con.execute("SELECT name FROM workbooks WHERE name LIKE ?", ["%" + SFX]).fetchall()
    _revert_same_row(con)
    for (name,) in rows:
        con.execute("INSERT INTO workbooks (name) VALUES (?)", [name[: -len(SFX)]])

def _name_keyed_insert(con):
    for name in ("EVO-A", "EVO-B"):
        if not con.execute("SELECT 1 FROM workbooks WHERE name = ?", [name]).fetchone():
            con.execute("INSERT INTO workbooks (name) VALUES (?)", [name])

def _nothing(con):
    pass

BEHAVIOURS = {"revert_same_row": (_revert_same_row, None), "revert_new_row": (_revert_new_row, None),
              "revert_and_copy": (_revert_and_copy, None), "name_keyed": (_name_keyed_insert, None),
              "helper_only": (_nothing, _name_keyed_insert), "helper_revert": (_nothing, _revert_same_row),
              "marker_keyed": (_nothing, None)}

class _FakeStart:
    def __init__(self, boot: str, lazy: str | None):
        self.ok, self.rc, self.why, self.text = True, 0, "", "init done"
        self.boot, self.lazy = boot, lazy

    def snaps(self):
        return [p for p in (self.boot, self.lazy) if p]

class _FakeCtx:
    def __init__(self, wd: str, boot_fn, lazy_fn, lines: list[str]):
        self.wd, self.db_name, self.log = wd, "database.db", lines.append
        self.project = self.backend = Path(wd)
        self.boot_fn, self.lazy_fn = boot_fn, lazy_fn

    def start(self, cur, data_dir, tag):
        db = os.path.join(data_dir, self.db_name)
        out = os.path.join(self.wd, "out-" + tag)
        os.makedirs(out)
        con = sqlite3.connect(db)
        self.boot_fn(con)
        con.commit()
        con.close()
        boot = os.path.join(out, "boot.db")
        shutil.copyfile(db, boot)
        lazy = None
        if self.lazy_fn:
            con = sqlite3.connect(db)
            self.lazy_fn(con)
            con.commit()
            con.close()
            lazy = os.path.join(out, "lazy.db")
            shutil.copyfile(db, lazy)
        return _FakeStart(boot, lazy)

def scenario_output(behaviour: str) -> dict:
    wd = tempfile.mkdtemp(prefix="v182-undo-")
    try:
        base = os.path.join(wd, "base", "database.db")
        os.makedirs(os.path.dirname(base))
        con = sqlite3.connect(base)
        con.execute("CREATE TABLE workbooks (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL)")
        con.executemany("INSERT INTO workbooks (name) VALUES (?)", [("Other",), ("EVO-A",), ("EVO-B",)])
        con.commit()
        con.close()
        lines: list[str] = []
        boot_fn, lazy_fn = BEHAVIOURS[behaviour]
        ctx = _FakeCtx(wd, boot_fn, lazy_fn, lines)
        st = dl._State(False)
        scen = {"S1": ["EVO-A"], "S2": ["EVO-B"]}
        hits = {"EVO-A": "backend/src/database/init_db.js:7", "EVO-B": "backend/src/database/init_db.js:7"}
        dl._probes(ctx, st, scen, {s: n[0] for s, n in scen.items()}, base, os.path.join(wd, "cur"), hits)
        return {"findings": st.findings(), "notes": st.notes, "lines": lines,
                "resurrection": st.paths["resurrection"]}
    finally:
        shutil.rmtree(wd, ignore_errors=True)

def kinds(out) -> list[str]:
    return [f["check"].split("/", 1)[1] for f in out["findings"]]

@case
def must_fire_same_row_undo_is_reported():
    with Switch(**{KEY: True}):
        out = scenario_output("revert_same_row")
    assert kinds(out) == ["renamed_reverted"], out
    f = out["findings"][0]
    assert set(f) == {"where", "requirement", "problem", "fix", "check"}, f
    assert f["where"] == "data-lifecycle: backend/src/database/init_db.js:7", f
    assert f["requirement"] == "S1" and "`EVO-A`" in f["problem"] and "`workbooks`" in f["problem"], f
    assert "same rowid" in f["problem"] and f["fix"] == dl.FIX_SEED, f
    assert out["resurrection"] == "failed", out
    assert not any("(log only)" in n for n in out["notes"]), out
    assert any("renamed the same row (rowid 2) back in place" in x for x in out["lines"]), out

@case
def default_is_on():
    from agent.settings import SETTINGS

    had, value = KEY in SETTINGS, SETTINGS.pop(KEY, None)
    try:
        assert kinds(scenario_output("revert_same_row")) == ["renamed_reverted"]
    finally:
        if had:
            SETTINGS[KEY] = value

@case
def must_stay_silent_identity_not_verified():
    with Switch(**{KEY: True}):
        for behaviour in ("revert_new_row", "revert_and_copy"):
            out = scenario_output(behaviour)
            assert out["findings"] == [], (behaviour, out)
            assert any("renamed the row back in place (log only)" in n for n in out["notes"]), (behaviour, out)
            assert out["resurrection"] == "ok", (behaviour, out)

@case
def must_stay_silent_helper_only_and_clean_seed():
    with Switch(**{KEY: True}):
        out = scenario_output("helper_only")
        assert out["findings"] == [], out
        assert any("back only after the ensure*/seed* helpers ran (log only)" in n for n in out["notes"]), out
        helper_revert = scenario_output("helper_revert")
        assert helper_revert["findings"] == [] and helper_revert["resurrection"] == "ok", helper_revert
        clean = scenario_output("marker_keyed")
        assert clean["findings"] == [] and clean["resurrection"] == "ok", clean

@case
def name_keyed_resurrection_unchanged():
    with Switch(**{KEY: True}):
        on = scenario_output("name_keyed")
    with Switch(**{KEY: False}):
        off = scenario_output("name_keyed")
    assert sorted(kinds(on)) == ["deleted_resurrects", "renamed_resurrects"], on
    assert on == off

@case
def off_is_the_old_log_only_note():
    with Switch(**{KEY: False}):
        out = scenario_output("revert_same_row")
    assert out["findings"] == [], out
    assert any("renamed the row back in place (log only)" in n for n in out["notes"]), out
    assert not any("rowid" in x for x in out["lines"]), out

@case
def off_equals_v181_byte_for_byte():
    if not (V181 / "agent" / "data_lifecycle.py").is_file():
        raise unittest.SkipTest(f"{V181} not present")
    names = sorted(BEHAVIOURS)
    with Switch(**{KEY: False}):
        mine = {b: scenario_output(b) for b in names}
    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); import cases_v182_lifecycle_undo as t; "
            "print(json.dumps({b: t.scenario_output(b) for b in sys.argv[2:]}))")
    env = dict(os.environ, V182_UNDO_ROOT=str(V181))
    res = subprocess.run([sys.executable, "-B", "-c", code, str(HERE)] + names, capture_output=True, text=True,
                         env=env, timeout=120)
    assert res.returncode == 0, res.stderr[-2000:]
    theirs = json.loads(res.stdout.strip().splitlines()[-1])
    assert json.loads(json.dumps(mine)) == theirs, (mine, theirs)

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
