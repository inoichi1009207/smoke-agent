from __future__ import annotations

import os
import re
import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.dont_write_bytecode = True

from agent import codegen, conventions, evolution, settings
from agent import data_lifecycle as dl
from agent import seed_foundation as sf
from agent.testrun import vacuous_absence_checks
from cases_v144l import BATCH, REL, spec

CASES = []
QUOTES = "[`'\"‘’“”]"
SHEET_SENTENCE = ("The visitor is unauthenticated and the system pre-provisions workbook `EVO-N02-FIND-NEXT` with active "
                  "worksheet `Narrative`, where E4, E7, and E11 contain `Cobalt`")

def case(function):
    CASES.append(function)
    return function

class Switch:

    def __init__(self, key: str, on: bool):
        self.key, self.on = key, on

    def __enter__(self):
        self.had = self.key in settings.SETTINGS
        self.saved = settings.SETTINGS.get(self.key)
        settings.SETTINGS[self.key] = self.on
        return self

    def __exit__(self, *exc):
        if not self.had:
            settings.SETTINGS.pop(self.key, None)
        else:
            settings.SETTINGS[self.key] = self.saved
        return False

class EditBlocks:
    def __init__(self, value: str):
        self.value = value

    def __enter__(self):
        self.saved = os.environ.get("AGENT_EDIT_BLOCKS")
        os.environ["AGENT_EDIT_BLOCKS"] = self.value
        return self

    def __exit__(self, *exc):
        if self.saved is None:
            os.environ.pop("AGENT_EDIT_BLOCKS", None)
        else:
            os.environ["AGENT_EDIT_BLOCKS"] = self.saved
        return False

def make_db(folder: str, statements: list[str], rows: list[tuple[str, tuple]]) -> str:
    path = os.path.join(folder, "app.db")
    con = sqlite3.connect(path)
    try:
        for sql in statements:
            con.execute(sql)
        for sql, values in rows:
            con.execute(sql, values)
        con.commit()
    finally:
        con.close()
    return path

def inv_of(*sentences: str) -> dict:
    scenarios = []
    for i, sentence in enumerate(sentences):
        hard, soft = sf.classify_literals(sentence)
        scenarios.append({"leaf": f"REQ-9-{i + 1}", "scenario": f"Scenario {i + 1}", "sentences": [sentence],
                          "names": hard, "advisory": soft})
    names: list[str] = []
    for row in scenarios:
        names += [n for n in row["names"] if n not in names]
    return {"scenarios": scenarios, "root": [], "names": names, "advisory": [],
            "extent": {"grid": False, "col": 0, "row": 0, "cell": ""}}

def synthetic() -> dict:
    sentence = "GIVEN the system pre-provisions organization `evo-ops-lab` with member `evo-ops-user`"
    inv = inv_of(sentence, SHEET_SENTENCE)
    inv["names"] = inv["names"] + ["evo-other-org"]
    return inv

def batch_like_main(inv: dict, members: list[str], seeds: str) -> dict:
    batch = dict(BATCH, members=members)
    batch["seed_sentences"] = seeds
    block = sf.batch_block(inv, [str(m) for m in (batch.get("members") or [batch.get("id")])])
    if block:
        batch["seed_sentences"] = (seeds + "\n\n" + block).strip()
    return batch

@case
def both_switches_are_registered_and_on():
    assert settings._DEFAULTS["fixture_idempotent"] is True
    assert settings._DEFAULTS["conventions_v167"] is True

@case
def a_workbook_seeded_twice_is_a_duplicate_fixture():
    kinds = {"EVO-N02-FIND-NEXT": "workbook", "EVO-N03-NAMED-CREATE": "workbook"}
    rows = [("insert into workbooks(name, active_sheet_id) values (?, 1)", (n,))
            for n in ("EVO-N02-FIND-NEXT", "EVO-N03-NAMED-CREATE", "EVO-N02-FIND-NEXT")]
    with tempfile.TemporaryDirectory() as tmp:

        db = make_db(tmp, ["create table workbooks(id integer primary key, name text, active_sheet_id integer)"], rows)
        assert dl.duplicate_fixtures(db, kinds) == {}
    with tempfile.TemporaryDirectory() as tmp:

        db = make_db(tmp, ["create table workbooks(id integer primary key, name text, active_sheet_id integer)",
                           "create table sheets(id integer primary key, workbook_id integer, name text)"], rows)
        assert dl.duplicate_fixtures(db, kinds) == {"EVO-N02-FIND-NEXT": (2, "workbooks", "name")}

        con = sqlite3.connect(db)
        con.execute("insert into workbooks(name) values (?)", ("  EVO-N03-NAMED-CREATE ",))
        con.commit()
        con.close()
        assert dl.duplicate_fixtures(db, kinds) == {"EVO-N02-FIND-NEXT": (2, "workbooks", "name"),
                                                    "EVO-N03-NAMED-CREATE": (2, "workbooks", "name")}

@case
def a_worksheet_name_in_three_workbooks_is_no_duplicate():
    kinds = sf.sentence_kinds(SHEET_SENTENCE)
    assert kinds == {"EVO-N02-FIND-NEXT": "workbook", "Narrative": "per-parent"}, kinds
    inv = inv_of(SHEET_SENTENCE)
    assert sf.name_kinds(inv) == kinds
    glob = sf.global_names(inv)
    assert glob == {"EVO-N02-FIND-NEXT": "workbook"}, glob
    assert "Narrative" not in sf.global_names(inv, ["Narrative", "EVO-N02-FIND-NEXT"])
    with tempfile.TemporaryDirectory() as tmp:
        db = make_db(tmp, ["create table workbooks(id integer primary key, name text)",
                           "create table worksheets(id integer primary key, workbook_id integer, name text)"],
                     [("insert into workbooks(id, name) values (?, ?)", (i, n))
                      for i, n in ((1, "EVO-N02-FIND-NEXT"), (2, "EVO-A"), (3, "EVO-B"))]
                     + [("insert into worksheets(workbook_id, name) values (?, 'Narrative')", (i,)) for i in (1, 2, 3)])
        assert dl.duplicate_fixtures(db, glob) == {}
        assert dl.duplicate_fixtures(db, {"Narrative": "per-parent"}) == {}
        assert dl.duplicate_fixtures(db, kinds) == {}

@case
def single_rows_are_no_duplicate():
    kinds = {"evo-ops-lab": "organization", "evo-ops-user": "account", "EVO-W": "workbook", "evo-repo": "repository",
             "evo-team": "team"}
    with tempfile.TemporaryDirectory() as tmp:
        db = make_db(tmp, ["create table organizations(id integer primary key, name text)",
                           "create table users(id integer primary key, username text)",
                           "create table workbooks(id integer primary key, title text)",
                           "create table repositories(id integer primary key, owner_id integer, name text)",
                           "create table teams(id integer primary key, name text)"],
                     [("insert into organizations(name) values ('evo-ops-lab')", ()),
                      ("insert into users(username) values ('evo-ops-user')", ()),
                      ("insert into workbooks(title) values ('EVO-W')", ()),
                      ("insert into repositories(owner_id, name) values (1, 'evo-repo')", ()),
                      ("insert into teams(name) values ('evo-team')", ())])
        assert dl.duplicate_fixtures(db, kinds) == {}
    assert dl.duplicate_fixtures("", kinds) == {} and dl.duplicate_fixtures(None, kinds) == {}

@case
def a_name_of_unknown_kind_is_never_checked():
    bare = "GIVEN the system pre-provisions `Mystery`"
    assert sf.sentence_kinds(bare) == {"Mystery": ""}
    both = inv_of("GIVEN the system pre-provisions workbook `Dual`", "GIVEN the system pre-provisions worksheet `Dual`",
                  bare)
    nk = sf.name_kinds(both)
    assert nk.get("Dual") == "" and nk.get("Mystery") == "", nk
    assert sf.global_names(both) == {}
    assert sf.name_kinds(None) == {} and sf.global_names({}) == {}

    one = "GIVEN the system pre-provisions workbook `Dual` with active worksheet `Dual`"
    assert sf.sentence_kinds(one) == {"Dual": sf.CONFLICT}
    conflicted = inv_of(one, "GIVEN the system pre-provisions workbook `Dual`")
    assert sf.name_kinds(conflicted) == {"Dual": ""} and sf.global_names(conflicted) == {}
    conflicted = inv_of("GIVEN the system pre-provisions workbook `Dual`", one)
    assert sf.name_kinds(conflicted) == {"Dual": ""} and sf.global_names(conflicted) == {}

    solo = inv_of("GIVEN the system pre-provisions workbook `Solo`", "GIVEN the system pre-provisions `Solo`")
    assert sf.name_kinds(solo) == {"Solo": "workbook"} and sf.global_names(solo) == {"Solo": "workbook"}
    with tempfile.TemporaryDirectory() as tmp:
        db = make_db(tmp, ["create table workbooks(id integer primary key, name text)"],
                     [("insert into workbooks(name) values (?)", (n,)) for n in ("Dual", "Dual", "Mystery", "Mystery")])
        assert dl.duplicate_fixtures(db, {"Dual": "", "Mystery": ""}) == {}
        assert dl.duplicate_fixtures(db, sf.global_names(both)) == {}
        assert dl.duplicate_fixtures(db, {"Dual": "workbook"}) == {"Dual": (2, "workbooks", "name")}

@case
def link_tables_and_owner_columns_repeat_names_legitimately():
    kinds = {"EVO-SHARED": "workbook", "evo-ops-user": "account"}
    with tempfile.TemporaryDirectory() as tmp:
        db = make_db(tmp, ["create table workbooks(id integer primary key, name text)",
                           "create table workbook_shares(workbook_id integer, name text)",
                           "create table org_members(org_id integer, username text)",
                           "create table user_roles(user_id integer, username text)",
                           "create table accounts(id integer primary key, login text, owner_name text)"],
                     [("insert into workbooks(name) values ('EVO-SHARED')", ()),
                      ("insert into accounts(login) values ('evo-ops-user')", ())]
                     + [("insert into workbook_shares values (1, 'EVO-SHARED')", ())] * 3
                     + [("insert into org_members values (?, 'evo-ops-user')", (i,)) for i in (1, 2)]
                     + [("insert into user_roles values (1, 'evo-ops-user')", ())] * 2
                     + [("insert into accounts(login, owner_name) values (?, 'evo-ops-user')", (f"other{i}",))
                        for i in (1, 2)])
        assert dl.duplicate_fixtures(db, kinds) == {}

@case
def a_scoped_table_is_never_checked_even_under_one_owner():
    kinds = {"acme-docs": "repository"}
    repo = "create table repositories(id integer primary key, owner_type text, owner_id integer, name text)"
    for owners in ((("org", 1), ("user", 10)), (("org", 1), ("user", 1)), (("org", 1), ("org", 1))):
        with tempfile.TemporaryDirectory() as tmp:
            db = make_db(tmp, [repo], [("insert into repositories(owner_type, owner_id, name) values (?, ?, 'acme-docs')",
                                        o) for o in owners])
            assert dl.duplicate_fixtures(db, kinds) == {}, owners

@case
def codex_counter_examples_are_no_duplicate_and_a_plain_users_table_is():
    with tempfile.TemporaryDirectory() as tmp:
        db = make_db(tmp, ["create table repositories(id integer primary key, name text, created_by integer)",
                           "create table team_users(team_id integer, user_id integer, username text)",
                           "create table teams(id integer primary key, name text, parent_uuid text)"],
                     [("insert into repositories(name, created_by) values ('acme-docs', ?)", (i,)) for i in (1, 2)]
                     + [("insert into team_users values (?, ?, 'evo-ops-user')", (t, 7)) for t in (1, 2)]
                     + [("insert into teams(name, parent_uuid) values ('evo-team', ?)", (u,)) for u in ("a", "b")])
        kinds = {"acme-docs": "repository", "evo-ops-user": "account", "evo-team": "team"}
        assert dl.duplicate_fixtures(db, kinds) == {}
        con = sqlite3.connect(db)
        con.execute("create table users(id integer primary key, username text)")
        con.executemany("insert into users(username) values (?)", [("evo-ops-user",), ("evo-ops-user",)])
        con.commit()
        con.close()
        assert dl.duplicate_fixtures(db, kinds) == {"evo-ops-user": (2, "users", "username")}
    with tempfile.TemporaryDirectory() as tmp:
        db = make_db(tmp, ["create table workbooks(id integer primary key, name text)"],
                     [("insert into workbooks(name) values ('EVO-W')", ())] * 2)
        con = sqlite3.connect(db)
        try:
            assert dl.top_level_entity(con, "workbooks", "name") is True
            assert dl.top_level_entity(con, "no_such_table", "name") is False
        finally:
            con.close()

@case
def the_sentence_is_in_the_lifecycle_note_once_never_in_the_batch_block_and_off_is_v166():
    sentence = sf.IDEMPOTENT_SENTENCE
    assert sentence.isascii() and "\n" not in sentence, sentence
    assert not re.search(QUOTES, sentence), sentence
    assert "EVO-" not in sentence and "evo-" not in sentence.lower()
    for inv in (synthetic(), inv_of(SHEET_SENTENCE)):
        for name in inv["names"] + inv["advisory"]:
            assert name not in sentence, name
        members = [inv["scenarios"][0]["leaf"]]
        with Switch("fixture_idempotent", True):
            on = sf.batch_block(inv, members)
            assert sf.idempotent_head() == " " + sentence
        with Switch("fixture_idempotent", False):
            off = sf.batch_block(inv, members)
            assert sf.idempotent_head() == ""
        assert on == off and sentence not in on
        assert on.split("\n", 1)[0] == sf.BLOCK_HEAD
    with Switch("fixture_idempotent", True):
        note = evolution.data_lifecycle_note()
    keep = "Every existing seed record stays exactly as it is."
    with_exceptions = evolution.DATA_LIFECYCLE_NOTE.replace(keep, keep[:-1] + ", " + sf.SEED_EXCEPTIONS + ".", 1)
    assert with_exceptions != evolution.DATA_LIFECYCLE_NOTE
    assert note == with_exceptions + " " + sentence and note.count(sentence) == 1
    with Switch("fixture_idempotent", False):
        assert evolution.data_lifecycle_note() == evolution.DATA_LIFECYCLE_NOTE
    with Switch("fixture_idempotent", "false"):
        assert evolution.data_lifecycle_note() == evolution.DATA_LIFECYCLE_NOTE
    assert sf.batch_block({}, ["REQ-1"]) == "" and sf.batch_block(inv_of(SHEET_SENTENCE), []) != ""

    conventions.set_evolution_round(True)
    try:
        with Switch("fixture_idempotent", True):
            evo_on = conventions.evolution_conventions()
        with Switch("fixture_idempotent", False):
            evo_off = conventions.evolution_conventions()
    finally:
        conventions.set_evolution_round(False)
    assert evo_off == "- " + conventions.EVOLUTION_DATA_LIFECYCLE + "\n"
    assert evo_on == "- " + conventions.EVOLUTION_DATA_LIFECYCLE + " " + sentence + "\n"
    assert conventions.evolution_conventions() == ""

@case
def no_name_reaches_the_stated_text_corpus():
    inv = synthetic()
    seeds = "SEED RECORDS: the requirement's own seed sentences"
    members = [inv["scenarios"][0]["leaf"]]
    with Switch("fixture_idempotent", False):
        off = batch_like_main(inv, members, seeds)
    with Switch("fixture_idempotent", True):
        on = batch_like_main(inv, members, seeds)
    assert on == off and sf.IDEMPOTENT_SENTENCE not in on["seed_sentences"]
    text = spec('  await notSee(page, "Evo Ops Lab");', '  await notSee(page, "evo-ops-lab");',
                '  await notSee(page, "Narrative Copy");')
    with Switch("fixture_idempotent", False):
        found_off = vacuous_absence_checks(REL, text, off)
    with Switch("fixture_idempotent", True):
        found_on = vacuous_absence_checks(REL, text, on)
    assert found_on == found_off and found_on, found_on

    words = ("seed foundation", "version key", "second record", "same owner or parent")
    text = spec(*[f'  await notSee(page, "{w}");' for w in words])
    with Switch("fixture_idempotent", False):
        found_off = vacuous_absence_checks(REL, text, off)
    with Switch("fixture_idempotent", True):
        found_on = vacuous_absence_checks(REL, text, on)
    assert found_on == found_off and len(found_on) == len(words), found_on
    leaked = dict(on, seed_sentences=on["seed_sentences"] + "\n  " + sf.IDEMPOTENT_SENTENCE)
    assert len(vacuous_absence_checks(REL, text, leaked)) < len(words)

@case
def the_slot_acceptance_compares_duplicates_per_name_and_count():
    import main as m

    dup = {"check": "data-lifecycle/duplicate_fixture", "problem": "duplicate fixture"}

    assert m.lifecycle_new_problems([dup], [dup], {"duplicate_counts": {"A": 2, "B": 3}}, {"A": 2}) == []
    assert m.lifecycle_new_problems([dup], [], {"duplicate_counts": {"A": 2}}, {}) == []
    assert m.lifecycle_new_problems([dup], [], {"duplicate_counts": {"A": 2}}) == []
    assert m.lifecycle_new_problems([dup], [dup], {"duplicate_counts": {"A": 2}}, {"A": 2}) == []
    assert m.lifecycle_new_problems([dup], [dup], {"duplicate_counts": {"A": 3}}, {"A": 2, "B": 2}) == []

    assert m.lifecycle_new_problems([dup], [dup], {"duplicate_counts": {"B": 2}}, {"A": 2, "B": 3}) == []

    assert m.lifecycle_new_problems([dup], [dup], {"duplicates": {"A": "9 rows in workbooks"}}, {"A": 2}) == []

    old = {"check": "data-lifecycle/restart_failed", "problem": "p0"}
    others = [{"check": "data-lifecycle/restart_failed", "problem": "p1"},
              {"check": "data-lifecycle/empty_start_failed", "problem": "p2" * 300}]
    got = m.lifecycle_new_problems(others, [old], {}, None)
    assert got == ["data-lifecycle/empty_start_failed: " + ("p2" * 300)[:400]], got
    assert m.lifecycle_new_problems(others[:1], [old], {"completed": True}) == []

    text = (ROOT / "main.py").read_text(encoding="utf-8")
    clean = [x.strip() for x in text.splitlines() if x.strip().startswith("if not missing0 and")]
    assert clean and all("not dups0" in x for x in clean), clean
    assert "if missing0 or dups0 or not plan_b:" in text
    assert "lifecycle_new_problems(others, others0, report, counts0)" in text
    assert "lifecycle_new_problems(others, others0, report, dups_a)" in text
    assert 'counts0 = dict(report0.get("duplicate_counts") or {})' in text
    assert 'dups = dict(report.get("duplicate_counts") or {})' in text

    assert "new_missing = [n for n in missing if n not in missing0]" in text
    assert "lost = [n for n in missing if n not in missing_a]" in text

@case
def codex_r2_scoped_schemas_are_never_duplicates():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "r2.db")
        con = sqlite3.connect(db)
        con.executescript("""
            create table repositories(owner_id integer, name text, primary key(owner_id, name));
            insert into repositories values (1, 'acme-docs'), (2, 'acme-docs');
            create table accounts(id integer primary key, username text);
            create table organizations(id integer primary key, name text, principal integer references accounts(id));
            insert into organizations(name, principal) values ('evo-org', 1), ('evo-org', 2);
            create table repos(id integer primary key, name text, owner_id integer);
            insert into repos(name, owner_id) values ('evo-repo', 1), ('evo-repo', 2);
            create table owners(id integer primary key, label text, repo_id integer);
            create table workbooks(id integer primary key, name text, active_sheet_id integer);
            insert into workbooks(name, active_sheet_id) values ('EVO-N02-FIND-NEXT', 1), ('EVO-N02-FIND-NEXT', 2);
            create table sheets(id integer primary key, workbook_id integer references workbooks(id), name text);
            create table books(id integer primary key, name text, owner_sheet_id integer);
            insert into books(name, owner_sheet_id) values ('EVO-BOOK', 1), ('EVO-BOOK', 2);
            create table owner_sheets(id integer primary key, book_id integer);
        """)
        con.commit()
        con.close()
        kinds = {"acme-docs": "repository", "evo-org": "organization", "evo-repo": "repository",
                 "EVO-N02-FIND-NEXT": "workbook", "EVO-BOOK": "workbook"}
        assert dl.duplicate_fixtures(db, kinds) == {"EVO-N02-FIND-NEXT": (2, "workbooks", "name")}
        con = sqlite3.connect(db)
        try:
            assert not dl.top_level_entity(con, "repositories", "name")
            assert not dl.top_level_entity(con, "organizations", "name")
            assert not dl.top_level_entity(con, "repos", "name")
            assert dl.top_level_entity(con, "workbooks", "name")
            assert not dl.top_level_entity(con, "books", "name")
        finally:
            con.close()
    assert dl.CHILD_POINTER.match("active_sheet_id") and not dl.CHILD_POINTER.match("owner_id")

    assert "check whether they belong to the same owner or parent" in dl.FIX_DUPLICATE
    assert "If they belong to different owners or parents they are separate records: change nothing" in dl.FIX_DUPLICATE
    assert "proven" not in dl.FIX_DUPLICATE
    text = (ROOT / "main.py").read_text(encoding="utf-8")
    assert '+ ". " + FIX_DUPLICATE)' in text and "These are proven duplicates" not in text
    assert 'problems.append("still held more than once' not in text

@case
def codex_r3_scopes_and_keyed_counts():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "r3.db")
        con = sqlite3.connect(db)
        con.executescript("""
            create table accounts(id integer primary key, username text);
            create table repositories(principal integer primary key references accounts(id), name text);
            insert into repositories values (1, 'acme-docs'), (2, 'acme-docs');
            create table owners(id integer primary key, label text, repo_id integer);
            create table repos(id integer primary key, name text, primary_owner_id integer);
            insert into repos(name, primary_owner_id) values ('evo-repo', 1), ('evo-repo', 2);
            create table workbooks(id integer primary key, name text, activeSheetId integer);
            insert into workbooks(name, activeSheetId) values ('EVO-CAMEL', 1), ('EVO-CAMEL', 2);
            create table worksheets(id integer primary key, workbook_id integer references workbooks(id), name text);
            create table spreadsheets(id integer primary key, name text, active_owner_id integer);
            insert into spreadsheets(name, active_owner_id) values ('EVO-OWNER', 1), ('EVO-OWNER', 2);
            create table owner(id integer primary key, spreadsheet_id integer);
        """)
        con.commit()
        con.close()
        kinds = {"acme-docs": "repository", "evo-repo": "repository", "EVO-CAMEL": "workbook", "EVO-OWNER": "workbook"}
        assert dl.duplicate_fixtures(db, kinds) == {"EVO-CAMEL": (2, "workbooks", "name")}
        con = sqlite3.connect(db)
        try:
            assert not dl.top_level_entity(con, "repositories", "name")
            assert not dl.top_level_entity(con, "repos", "name")
            assert dl.top_level_entity(con, "workbooks", "name")
            assert not dl.top_level_entity(con, "spreadsheets", "name")
        finally:
            con.close()
    for col in ("active_sheet_id", "activeSheetId", "currentSheetId", "selected_sheet_id"):
        assert dl.CHILD_POINTER.match(col), col
    for col in ("primary_owner_id", "default_sheet_id", "primary_sheet_id", "owner_id", "sheet_id"):
        assert not dl.CHILD_POINTER.match(col), col

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "p2.db")
        con = sqlite3.connect(db)
        con.executescript("""
            create table repositories(id integer primary key, name text);
            insert into repositories(name) values ('A'), ('A'), ('A');
            create table repos(id integer primary key, name text);
            insert into repos(name) values ('A'), ('A');
        """)
        con.commit()
        con.close()
        places = dl._duplicate_places(db, {"A": "repository"})
        assert places == {("repository", "repositories", "name", "A"): 3, ("repository", "repos", "name", "A"): 2}, places
        assert dl.duplicate_fixtures(db, {"A": "repository"}) == {"A": (3, "repositories", "name")}
    import main as m

    k1, k2 = dl.count_key("repository", "repositories", "name", "A"), dl.count_key("repository", "repos", "name", "A")
    dup = {"check": "data-lifecycle/duplicate_fixture", "problem": "duplicate fixture"}

    assert m._count_label(k2, 2, 1) == "`A` (repos.name: 2 rows, was 1)"
    assert m.lifecycle_new_problems([dup], [dup], {"duplicate_counts": {k1: 3, k2: 2}}, {k1: 3}) == []
    assert m.lifecycle_new_problems([dup], [dup], {"duplicate_counts": {k1: 4}}, {k1: 3}) == []

    text = (ROOT / "main.py").read_text(encoding="utf-8")
    assert 'blocking = [f for f in again if not str(f.get("check") or "").endswith("/duplicate_fixture")]' in text
    assert "or st.duplicates) else" not in (ROOT / "agent/data_lifecycle.py").read_text(encoding="utf-8")

def _dups(sql: str, kinds: dict) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "r4.db")
        con = sqlite3.connect(db)
        con.executescript(sql)
        con.commit()
        con.close()
        return dl.duplicate_fixtures(db, kinds)

@case
def codex_r4_child_pointer_is_structural():
    kinds = {"W": "workbook"}

    assert _dups("""
        create table owners(id integer primary key);
        create table workbooks(id integer primary key, name text, active_sheet_id integer references owners(id));
        insert into workbooks(name, active_sheet_id) values ('W', 1), ('W', 2);
        create table sheets(id integer primary key, workbook_id integer, name text);""", kinds) == {}

    assert _dups("""
        create table repositories(id integer primary key, name text, current_project_id integer);
        insert into repositories(name, current_project_id) values ('A', 1), ('A', 2);
        create table projects(id integer primary key, default_repository_id integer);""", {"A": "repository"}) == {}

    assert _dups("""
        create table repositories(id integer primary key, name text, current_board_id integer);
        insert into repositories(name, current_board_id) values ('A', 1), ('A', 2);
        create table boards(id integer primary key, repository_id integer);""", {"A": "repository"}) == {}

    assert _dups("""
        create table workbooks(id integer primary key, name text, active_sheet_id integer);
        insert into workbooks(name, active_sheet_id) values ('W', 1), ('W', 2);
        create table worksheets(id integer primary key, workbook_id integer, name text);
        create table sheets(id integer primary key, label text);""", kinds) == {}

    assert _dups("""
        create table workbooks(id integer primary key, name text, active_sheet_id integer references worksheets(id));
        insert into workbooks(name, active_sheet_id) values ('W', 1), ('W', 2);
        create table worksheets(id integer primary key, workbook_id integer references workbooks(id), name text);
        create table sheets(id integer primary key, label text);""", kinds) == {"W": (2, "workbooks", "name")}

    assert _dups("""
        create table workbooks(id integer primary key, name text, active_sheet_id integer);
        insert into workbooks(name, active_sheet_id) values ('W', 1), ('W', 2);
        create table sheets(id integer primary key, primary_workbook_id integer, name text);""", kinds) == {}
    assert _dups("""
        create table books(id integer primary key);
        create table workbooks(id integer primary key, name text, active_sheet_id integer);
        insert into workbooks(name, active_sheet_id) values ('W', 1), ('W', 2);
        create table sheets(id integer primary key, workbook_id integer references books(id), name text);""", kinds) == {}

    assert _dups("""
        create table workbooks(id integer primary key, name text, created_at text, updated_at text, active_sheet_id integer);
        insert into workbooks(name, active_sheet_id) values ('W', 1), ('W', 2);
        create table sheets(id integer primary key, workbook_id integer references workbooks(id), name text);""",
                 kinds) == {"W": (2, "workbooks", "name")}

def _calls(main: str) -> list[tuple[str, str]]:
    lines = main.splitlines()
    out = []
    for i, line in enumerate(lines):
        if "lifecycle_check(" not in line or "import" in line or line.lstrip().startswith(("#", "def ", '"""')):
            continue
        if "= lifecycle_check(" not in line:
            continue
        depth, j, chunk = 0, i, []
        while j < len(lines):
            chunk.append(lines[j])
            depth += lines[j].count("(") - lines[j].count(")")
            if depth <= 0:
                break
            j += 1
        out.append(("\n".join(chunk), "\n".join(lines[max(0, i - 6):i])))
    return out

@case
def main_passes_name_kinds_only_under_the_switch_and_uses_the_note_function():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    calls = _calls(main)
    assert len(calls) == 2, [c[0][:80] for c in calls]
    for call, before in calls:
        assert '**({"name_kinds": kinds} if kinds else {})' in call, call
        assigns = [x.strip() for x in before.splitlines() if x.strip().startswith("kinds =")]
        assert len(assigns) == 1, before

        stmt = before[before.index("kinds ="):]
        stmt = stmt.split("findings, report", 1)[0]
        assert 'setting_on("fixture_idempotent")' in stmt and "else {}" in stmt, stmt
        assert "seed_foundation.global_names(" in stmt, stmt
    users = [x.strip() for x in main.splitlines() if "name_kinds" in x]
    assert len(users) == 2, users
    assert "evolution.data_lifecycle_note()" in main
    assert "evolution.DATA_LIFECYCLE_NOTE" not in main

@case
def lifecycle_check_without_name_kinds_has_no_duplicates_key():
    with tempfile.TemporaryDirectory() as tmp:
        quiet = lambda *a, **k: None
        findings, report = dl.lifecycle_check(tmp, "", scenario_names={"Scenario 1": ["EVO-X"]}, log=quiet)
        assert findings == [] and "duplicates" not in report, report
        findings, report = dl.lifecycle_check(tmp, "", scenario_names={"Scenario 1": ["EVO-X"]}, log=quiet,
                                              name_kinds={})
        assert "duplicates" not in report, report
        findings, report = dl.lifecycle_check(tmp, "", scenario_names={"Scenario 1": ["EVO-X"]}, log=quiet,
                                              name_kinds={"EVO-X": ""})
        assert "duplicates" not in report, report
        findings, report = dl.lifecycle_check(tmp, "", scenario_names={"Scenario 1": ["EVO-X"]}, log=quiet,
                                              name_kinds={"EVO-X": "workbook"})
        assert report.get("duplicates") == {}, report
    assert "duplicate_fixture" in dl.KIND_ORDER

@case
def the_v167_convention_line_is_in_the_implementer_prompt_once_when_on():
    lines = conventions.V167_LINES
    assert lines and conventions.V167_CONVENTIONS == "".join(x + "\n" for x in lines)
    for line in lines:
        assert line.startswith("- ") and line.isascii() and "\n" not in line, line

        assert not re.search(QUOTES, re.sub(r"(?<=[A-Za-z])'(?=[A-Za-z])", "", line)), line
        assert not re.search(r"\b(evo|acme|REQ-)", line, re.I), line
        assert not re.search(r"\b(evo|acme|REQ-)", line, re.I), line
        assert line not in conventions.PROJECT_CONVENTIONS
    for edit_env, plain in (("true", codegen.SYSTEM_PROMPT), ("false", codegen.SYSTEM_PROMPT_WHOLE_FILES)):
        with EditBlocks(edit_env), Switch("conventions_v166", True):
            with Switch("conventions_v167", True), Switch("choice_select_hybrid", False), Switch("nav_entry_data_driven", False), Switch("state_disables_everywhere", False), Switch("conventions_v168x", False):
                text = codegen.system_prompt()
                assert all(text.count(line) == 1 for line in lines)
                assert text == plain.replace(conventions.PROJECT_CONVENTIONS, conventions.PROJECT_CONVENTIONS
                                             + conventions.V166_CONVENTIONS + conventions.V167_CONVENTIONS, 1)
            with Switch("conventions_v167", False):
                text = codegen.system_prompt()
                assert all(text.count(line) == 0 for line in lines)
                assert text == plain.replace(conventions.PROJECT_CONVENTIONS,
                                             conventions.PROJECT_CONVENTIONS + conventions.V166_CONVENTIONS, 1)
        with EditBlocks(edit_env), Switch("conventions_v166", False), Switch("conventions_v167", True):
            assert codegen.system_prompt() == plain

@case
def consistency_audit_wording_is_behind_the_switch():
    sentence = sf.IDEMPOTENT_SENTENCE
    for part in ("first run of a migration that is not yet applied", "stable version key", "never afterwards",
                 "same owner or parent"):
        assert part in sentence, part
    for part in ("Only if they do", "keep the valid seed record", "remove only the second creation",
                 "Do not merge, rename or remove any other record", "stable version key"):
        assert part in dl.FIX_DUPLICATE, part
    assert "two elements" not in dl.FIX_DUPLICATE
    for text in (sf.SEED_EXCEPTIONS, dl.FIX_DUPLICATE, sentence):
        assert text.isascii() and not re.search(QUOTES, text) and "`" not in text, text
    inv = synthetic()
    with Switch("fixture_idempotent", False):
        assert sf.keep_seed_rule() == sf.KEEP_SEED_RULE
        off = sf.foundation_text(inv)
    with Switch("fixture_idempotent", True):
        assert sf.keep_seed_rule().startswith(sf.KEEP_SEED_RULE[:-1]) and sf.SEED_EXCEPTIONS in sf.keep_seed_rule()
        on = sf.foundation_text(inv)
    assert sf.KEEP_SEED_RULE in off.splitlines()
    assert on == off.replace(sf.KEEP_SEED_RULE, sf.KEEP_SEED_RULE[:-1] + ", " + sf.SEED_EXCEPTIONS + ".", 1)
    import main as m

    factory = m.Factory.__new__(m.Factory)
    with Switch("fixture_idempotent", False):
        assert factory.seed_b_rule() == m.Factory.SEED_B_RULE
    with Switch("fixture_idempotent", True):
        rule = factory.seed_b_rule()
    assert m.Factory.SEED_B_RULE.endswith(m.Factory.SEED_B_LIST) and rule == m.Factory.SEED_B_RULE[:-len(m.Factory.SEED_B_LIST)]
    assert "list the references" not in rule and "GRID WIDENING RULE" in rule
    assert "self.seed_b_rule()" in (ROOT / "main.py").read_text(encoding="utf-8")

def main() -> int:
    if not __debug__:
        print("FAIL  self-tests need assertions: run without -O and without PYTHONOPTIMIZE")
        return 1
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=6)
    print(f"{len(CASES) - failed}/{len(CASES)} v167 duplicate-fixture / convention cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
