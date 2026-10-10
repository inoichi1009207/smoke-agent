from __future__ import annotations

import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from agent import data_lifecycle as dl

CASES = []

def case(fn):
    CASES.append(fn)
    return fn

def _db(statements: list[str]) -> str:
    path = tempfile.mktemp(suffix=".db")
    con = sqlite3.connect(path)
    for s in statements:
        con.execute(s)
    con.commit()
    con.close()
    return path

ORGS = "create table organizations (id integer primary key, name text, display_name text)"
LOG = "create table audit_events (id integer primary key, action text, target text)"

@case
def an_invented_display_name_is_misplaced():
    path = _db([ORGS, "insert into organizations (name, display_name) values ('evo-audit-org', 'Evo Audit Org')",
                LOG, "insert into audit_events (action, target) values ('Repository created', 'evo-audit-org')"])
    got = dl.display_misplaced([path], ["evo-audit-org"])
    assert "evo-audit-org" in got and "Evo Audit Org" in got["evo-audit-org"], got

@case
def display_equal_empty_or_stated_is_not():
    for value in ("'evo-audit-org'", "NULL", "''", "'  '"):
        path = _db([ORGS, f"insert into organizations (name, display_name) values ('evo-audit-org', {value})"])
        assert dl.display_misplaced([path], ["evo-audit-org"]) == {}, value

    path = _db([ORGS, "insert into organizations (name, display_name) values ('acme', 'Acme Demo')"])
    assert dl.display_misplaced([path], ["acme", "Acme Demo"]) == {}

@case
def accounts_and_tables_without_display_name_are_not():
    path = _db(["create table users (id integer primary key, username text, display_name text)",
                "insert into users (username, display_name) values ('evo-audit-owner', 'Evo Audit Owner')"])
    assert dl.display_misplaced([path], ["evo-audit-owner"]) == {}
    path = _db(["create table repositories (id integer primary key, name text, description text)",
                "insert into repositories (name, description) values ('evo-repo', 'Evo Repo')"])
    assert dl.display_misplaced([path], ["evo-repo"]) == {}

@case
def the_alias_rule_of_v162a_is_unchanged():
    path = _db(["create table workbooks (id integer primary key, title text, evo_code text)",
                "insert into workbooks (title, evo_code) values ('FY26 Procurement Ledger', 'EVO-M01-RENAME-OK')"])
    got = dl.display_misplaced([path], ["EVO-M01-RENAME-OK"])
    assert "EVO-M01-RENAME-OK" in got and "evo_code" in got["EVO-M01-RENAME-OK"], got

@case
def case_or_spacing_only_and_people_tables_are_not():
    for value in ("'Evo-Audit-Org'", "'EVO-AUDIT-ORG'", "'evo-audit-org  '"):
        path = _db([ORGS, f"insert into organizations (name, display_name) values ('evo-audit-org', {value})"])
        assert dl.display_misplaced([path], ["evo-audit-org"]) == {}, value
    path = _db([ORGS, "insert into organizations (name, display_name) values ('acme', 'acme  DEMO')"])
    assert dl.display_misplaced([path], ["acme", "Acme Demo"]) == {}
    for table in ("contacts", "employees", "customers", "repo_owners"):
        path = _db([f"create table {table} (id integer primary key, name text, display_name text)",
                    f"insert into {table} (name, display_name) values ('jdoe', 'Jane Doe')"])
        assert dl.display_misplaced([path], ["jdoe"]) == {}, table

    path = _db([ORGS, "insert into organizations (name, display_name) values ('evo-audit-org', 'Evo Audit Org')"])
    got = dl.display_misplaced([path], ["evo-audit-org"])
    assert dl.DISPLAY_NAME_EVIDENCE in got["evo-audit-org"], got

@case
def the_foundation_slot_never_rolls_back_for_a_display_name_misplacement():
    import inspect

    import main as m

    src = inspect.getsource(m)
    i = src.index("DISPLAY_NAME_EVIDENCE in str(w)")
    block = src[i - 600:i + 4000]

    for word in ("hard_misplaced0", "new_hard", "damage = bool(problems)", "leftovers", "progress",
                 "problems += leftovers", "missing0_hard"):
        assert word in block, word
    assert 'seed_state["only_soft"]' in src and 'keep_if=lambda: seed_state.get("only_soft", False)' in src
    assert 'setting_on("seed_slot_keep_leftovers")' in src

@case
def repair_round_keeps_the_work_only_when_keep_if_says_so():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import main as m
    import cases_rollback as rb

    class GaveUp:
        def __init__(self, *a, **k):
            pass

        def implement(self, *a, **k):
            (self_root / rb.APP).write_text("APP changed by the round", encoding="utf-8")
            return m.ImplementResult("gave_up", [], "rounds used up")

    saved = m.Implementer
    m.Implementer = GaveUp
    try:
        for keep, expected in ((True, "APP changed by the round"), (False, "APP before")):
            with tempfile.TemporaryDirectory() as tmp:
                self_root = Path(tmp)
                globals()["self_root"] = self_root
                f = rb.make_factory(self_root, "APP before")
                kept = f.repair_round("seed-foundation", lambda: m.StepResult(False, "x"), "x", keep_if=lambda: keep)
                assert kept is keep, (keep, kept)
                assert (self_root / rb.APP).read_text(encoding="utf-8") == expected, keep

        with tempfile.TemporaryDirectory() as tmp:
            self_root = Path(tmp)
            globals()["self_root"] = self_root
            f = rb.make_factory(self_root, "APP before")
            assert f.repair_round("seed-foundation", lambda: m.StepResult(False, "x"), "x") is False
            assert (self_root / rb.APP).read_text(encoding="utf-8") == "APP before"
    finally:
        m.Implementer = saved

def main() -> int:
    failed = 0
    for fn in CASES:
        try:
            fn()
            print(f"ok    {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc(limit=6)
    print(f"{len(CASES) - failed}/{len(CASES)} v168z display cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
