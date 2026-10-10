from __future__ import annotations

import inspect
import os
import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

CASES = []

def case(function):
    CASES.append(function)
    return function

_MISSING = object()

class Patch:
    def __init__(self):
        self.saved = []

    def set(self, obj, name, value):
        self.saved.append((obj, name, getattr(obj, name, _MISSING)))
        setattr(obj, name, value)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        for obj, name, old in reversed(self.saved):
            if old is _MISSING:
                delattr(obj, name)
            else:
                setattr(obj, name, old)

def settings_with(**values):
    from agent import settings

    class Ctx:
        def __enter__(self):
            self.saved = dict(settings.SETTINGS)
            settings.SETTINGS.update(values)

        def __exit__(self, *exc):
            settings.SETTINGS.clear()
            settings.SETTINGS.update(self.saved)
    return Ctx()

class Logs:
    def __init__(self):
        import main as m
        self.m, self.lines = m, []

    def __enter__(self):
        self.saved = self.m.log
        self.m.log = self.lines.append
        return self.lines

    def __exit__(self, *exc):
        self.m.log = self.saved

def factory(project: Path, implement=("REQ-1",), req_text: str = "id: ROOT\n"):
    import main as m

    f = m.Factory.__new__(m.Factory)
    f.project_dir = project
    project.mkdir(parents=True, exist_ok=True)
    f.report, f.open_findings = {}, {}
    req = project.parent / (project.name + "-req")
    req.mkdir(parents=True, exist_ok=True)
    (req / "requirements.yaml").write_text(req_text, encoding="utf-8")
    f.requirements_dir = req
    f.evolution = {"active": True, "implement": list(implement), "classes": {"classes": {}}}
    f.note_duration = lambda *a, **k: None
    return f

SHEET_LEAF = {
    "id": "REQ-1", "name": "Rename workbook", "description": "Workbooks can be renamed. Cells K8 and L3 hold totals; data through row 40.",
    "scenarios": [
        {"name": "REQ-1 - trim and persist", "steps": [
            {"keyword": "GIVEN", "content": "The visitor is unauthenticated and the system pre-provisions workbook `EVO-M01-RENAME-OK` "
                                            "with active worksheet `IdentityLog` and cell F3 value `unreviewed`."},
            {"keyword": "WHEN", "content": "The visitor enters `  FY26 Procurement Ledger  ` in \"Workbook name\" and clicks \"Save\"."}]},
        {"name": "REQ-1 - duplicate", "steps": [
            {"keyword": "GIVEN", "content": "The system pre-provisions workbook `EVO-M01-RENAME-DUP` with active worksheet `IdentityLog`, "
                                            "plus a separate workbook named `EVO-M01-ARCHIVE-RESERVED`."}]},
        {"name": "REQ-1 - no fixture", "steps": [{"keyword": "GIVEN", "content": "The visitor opens the home page."}]}]}
TREE = {"id": "ROOT", "description": "Every scenario uses an independent EVO-prefixed pre-provisioned workbook and must remain "
                                     "independent of the baseline evaluation seed. Other text.",
        "children": [SHEET_LEAF, {"id": "REQ-2", "name": "Inherited", "description": "x", "scenarios": [
            {"name": "REQ-2 s", "steps": [{"keyword": "GIVEN", "content": "The system pre-provisions workbook `OLD-ONE`."}]}]}]}

@case
def the_v162a_switches_are_registered_with_their_defaults():
    from agent import settings

    assert settings.SETTINGS["evolution_seed_foundation"] is True
    assert settings.SETTINGS["evolution_seed_foundation_sec"] == 1500
    assert settings.SETTINGS["evolution_full_batch_window"] is True

@case
def an_evolution_round_gets_the_whole_run_target_as_its_batch_window():
    with tempfile.TemporaryDirectory() as tmp:
        f = factory(Path(tmp) / "app")
        f.stage_target = 54000.0
        own = 1800 + 2700 * 10 + 300.0
        with Logs() as lines:
            got = f.staged_batch_budget(own, 10, True)
        assert got == 54000 - 2700, got
        assert any("[factory] evolution: batch window 51300 s (run target 54000 s - closing reserve 2700 s)" in l for l in lines), lines
        assert f.staged_batch_budget(own, 10, False) == own
        with settings_with(evolution_full_batch_window=False):
            assert f.staged_batch_budget(own, 10, True) == own
        f.stage_target = 9000.0
        assert f.staged_batch_budget(own, 10, False) == 6300.0 and f.staged_batch_budget(own, 20, False) == 12000.0
        with settings_with(stage_closing_reserve_sec=3600):
            f.stage_target = 54000.0
            with Logs():
                assert f.staged_batch_budget(own, 10, True) == 50400.0
    import main as m
    src = inspect.getsource(m.Factory.run)
    assert "self.time_budget = self.staged_batch_budget(self.time_budget, len(work), bool(evo))" in src

REAL = {
    "github": (Path("local-data/evolution/req/hackathon-evolution--github"), Path("local-data/analysis-1006/github-full/ws/template")),
    "sheet": (Path("local-data/evolution/req/hackathon-evolution--sheet"), Path("local-data/analysis-1006/sheet/ws332/template")),
}

def real_inventory(task):
    import hashlib
    import json

    import yaml
    from agent import evolution as ev, seed_foundation as sf

    req, app = REAL[task]
    raw = (req / "requirements.yaml").read_bytes()
    tree = yaml.safe_load(raw.decode("utf-8"))
    old, _ = ev.inherited_requirement(app, hashlib.sha256(raw).hexdigest())
    handoff = json.loads((app / ".arc-handoff" / "handoff.json").read_text(encoding="utf-8"))
    got = ev.classify(tree, ev.historical_inventory(tree, handoff, old, app, ev.inherited_spec_files(app)), old)
    return sf.inventory(tree, ev.to_implement(got)), ev.to_implement(got)

@case
def the_inventory_of_the_real_evolution_requirements():
    present = [t for t, (req, app) in REAL.items() if (req / "requirements.yaml").is_file() and app.is_dir()]
    if not present:
        import unittest
        raise unittest.SkipTest("the evolution requirements / inherited deliverables are not on this machine")
    for task in present:
        inv, ids = real_inventory(task)
        assert len(ids) == 10
        if task == "github":
            assert len(inv["scenarios"]) == 28 and len(inv["names"]) == 36, (len(inv["scenarios"]), len(inv["names"]))
            assert not inv["extent"]["grid"]
            assert "evo-session-owner-s2" in inv["names"] and "evo-org-owner" in inv["names"]
            assert "Evo-Password-987!" not in inv["names"] and not any("@" in n for n in inv["names"])
            assert any("predefined seed data" in s for s in inv["root"])
        else:
            assert len(inv["scenarios"]) == 30 and len(inv["names"]) == 48, (len(inv["scenarios"]), len(inv["names"]))
            ext = inv["extent"]
            assert ext["grid"] and ext["col"] >= 13 and ext["row"] >= 40 and ext["cell"] == "M40", ext
            fy = [r for r in inv["scenarios"] if r["leaf"] == "REQ-1-2-2" and "trim and persist" in r["scenario"]]
            assert len(fy) == 1 and fy[0]["names"] == ["EVO-M01-RENAME-OK", "IdentityLog"], fy
            assert "FY26 Procurement Ledger" not in inv["names"] + inv["advisory"]
            assert "unreviewed" in fy[0]["advisory"]
            assert all(n.startswith("EVO-") or n[0].isupper() for n in inv["names"])
            assert any("EVO-prefixed" in s for s in inv["root"])

@case
def record_names_are_only_literals_in_record_position():
    from agent.seed_foundation import classify_literals

    hard, soft = classify_literals("The system pre-provisions the verified account `evo-a` with email `a@x.test` and password "
                                   "`Secret-123!`; `b@x.test` is unused.")
    assert hard == ["evo-a"] and soft == ["a@x.test", "b@x.test"], (hard, soft)
    hard, soft = classify_literals("The system pre-provisions accounts `u1`, `u2`, and `u3` with cell A1 value `42` and formula `=A1+1`.")
    assert hard == ["u1", "u2", "u3"] and soft == ["42", "=A1+1"], (hard, soft)
    hard, soft = classify_literals("The system pre-provisions repository `demo` with branch `main`, file `README.md` and "
                                   "the label text `Ready to ship`.")
    assert hard == ["demo", "main"] and "README.md" in soft and "Ready to ship" in soft, (hard, soft)
    hard, _ = classify_literals("The system pre-provisions `evo-session-owner-s2` with email `x@y.test`.")
    assert hard == ["evo-session-owner-s2"]

@case
def the_batch_block_carries_own_sentences_and_every_other_name_for_the_lock():
    from agent import seed_foundation as sf
    from agent.seed_values import lost_values, stated_seed_values

    inv = sf.inventory(TREE, ["REQ-1"])
    assert [r["scenario"] for r in inv["scenarios"]] == ["REQ-1 - trim and persist", "REQ-1 - duplicate"]
    assert inv["names"] == ["EVO-M01-RENAME-OK", "IdentityLog", "EVO-M01-RENAME-DUP", "EVO-M01-ARCHIVE-RESERVED"]
    assert "OLD-ONE" not in inv["names"]
    assert inv["extent"]["cell"] == "L40", inv["extent"]
    own = sf.batch_block(inv, ["REQ-1"])
    assert own.startswith(sf.BLOCK_HEAD) and "- (REQ-1 / REQ-1 - trim and persist) The visitor is unauthenticated" in own
    assert "EVO-prefixed" in own and "Records other scenarios" not in own
    other = sf.batch_block(inv, ["REQ-9"])
    assert "- Records other scenarios of this round pre-provision" in other and "`EVO-M01-RENAME-DUP`" in other
    assert "The visitor is unauthenticated" not in other
    values = stated_seed_values("SEED SENTENCES ...\n- `alice`\n\n" + own)
    assert "EVO-M01-RENAME-OK" in values and "IdentityLog" in values and "unreviewed" in values
    before = {"backend/src/database/init_db.js": b"insert workbook 'EVO-M01-RENAME-OK' sheet 'IdentityLog'"}
    after = {"backend/src/database/init_db.js": b"insert workbook 'FY26 Procurement Ledger' sheet 'IdentityLog'"}
    assert lost_values(own, before, after) == ["EVO-M01-RENAME-OK"]
    assert lost_values(other, before, after) == ["EVO-M01-RENAME-OK"]
    assert sf.batch_block({"scenarios": []}, ["REQ-1"]) == ""
    text = sf.foundation_text(inv)
    assert "ONE versioned, idempotent data migration" in text and "up to L40" in text and "never an alias" in text
    assert "Do NOT implement any feature" in text and "EVO-M01-ARCHIVE-RESERVED" in text
    assert sf.summary(inv).startswith("[seed-foundation] 2 scenario(s), 4 record name(s)") and sf.summary(inv).endswith("sheet extent L40")
    assert sf.scenario_names(inv, ["REQ-1"]) == {"REQ-1 - trim and persist": ["EVO-M01-RENAME-OK", "IdentityLog"],
                                                 "REQ-1 - duplicate": ["EVO-M01-RENAME-DUP", "IdentityLog", "EVO-M01-ARCHIVE-RESERVED"]}
    assert sf.scenario_names(inv, ["REQ-2"]) == {}
    assert sf.inventory(None, ["REQ-1"])["scenarios"] == [] and sf.inventory({"children": 5}, None)["names"] == []

def make_db(path: Path, rows):
    con = sqlite3.connect(path)
    con.execute("create table workbooks (id integer primary key, name text, evo_code text)")
    con.execute("create table worksheets (id integer primary key, workbook_id int, name text)")
    con.execute("create table notes (id integer primary key, body text)")
    for name, code in rows:
        con.execute("insert into workbooks (name, evo_code) values (?, ?)", (name, code))
    con.execute("insert into worksheets (workbook_id, name) values (1, 'IdentityLog')")
    con.commit()
    con.close()

@case
def an_alias_only_record_under_another_display_name_is_missing():
    from agent.data_lifecycle import display_misplaced

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "a.db"
        make_db(db, [("FY26 Procurement Ledger", "EVO-M01-RENAME-OK"), ("EVO-M01-RENAME-DUP", "EVO-M01-RENAME-DUP"),
                     ("Shown", None)])
        got = display_misplaced([str(db)], ["EVO-M01-RENAME-OK", "EVO-M01-RENAME-DUP", "IdentityLog", "Shown", "absent"])
        assert list(got) == ["EVO-M01-RENAME-OK"], got
        assert "`evo_code`" in got["EVO-M01-RENAME-OK"] and "FY26 Procurement Ledger" in got["EVO-M01-RENAME-OK"]

        con = sqlite3.connect(db)
        con.execute("insert into notes (body) values ('EVO-M01-RENAME-OK')")
        con.commit()
        con.close()
        assert display_misplaced([str(db)], ["EVO-M01-RENAME-OK"]) == {}
    with tempfile.TemporaryDirectory() as tmp:

        db = Path(tmp) / "b.db"
        con = sqlite3.connect(db)
        con.execute("create table fixtures (id integer primary key, fixture_key text, payload text)")
        con.execute("insert into fixtures (fixture_key, payload) values ('EVO-X', '{}')")
        con.commit()
        con.close()
        assert display_misplaced([str(db)], ["EVO-X"]) == {}
        assert display_misplaced([str(Path(tmp) / "missing.db")], ["EVO-X"]) in ({}, {})

@case
def the_strict_pass_checks_every_name_and_reports_once():
    from agent import data_lifecycle as dl

    class Ctx:
        def __init__(self):
            self.lines = []
            self.log = self.lines.append

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "a.db"
        make_db(db, [("FY26 Procurement Ledger", "EVO-M01-RENAME-OK"), ("EVO-M01-RENAME-DUP", None)])
        corpus = dl.db_corpus([str(db)])
        st = dl._State(False)
        st.strict = True
        ctx = Ctx()
        scen = {"s1": ["EVO-M01-RENAME-OK", "IdentityLog"], "s2": ["EVO-M01-RENAME-DUP", "IdentityLog", "EVO-M01-ARCHIVE-RESERVED"],
                "s3": ["EVO-ONLY-IN-SOURCE"], "s4": ["EVO-VACANT"]}
        dl._strict_missing(ctx, st, scen, corpus, {"EVO-ONLY-IN-SOURCE": "seed.js"}, {"EVO-VACANT"}, [str(db)], Path(tmp), Path(tmp))
        assert st.missing == {"s1": ["EVO-M01-RENAME-OK"], "s2": ["EVO-M01-ARCHIVE-RESERVED"]}, st.missing
        kinds = [k for k, _ in st.items]
        assert kinds == ["scenario_seed_missing"], kinds
        problem = st.items[0][1]["problem"]
        assert "`EVO-M01-ARCHIVE-RESERVED`" in problem and "ANOTHER display name" in problem and "evo_code" in problem
        assert any("EVO-ONLY-IN-SOURCE" in n for n in st.notes) and "EVO-VACANT" not in problem
        assert set(st.misplaced) == {"EVO-M01-RENAME-OK"}

        st2 = dl._State(False)
        st2.strict = True
        dl._strict_missing(ctx, st2, {"s": ["EVO-M01-RENAME-DUP", "IdentityLog"]}, corpus, {}, set(), [str(db)], Path(tmp), Path(tmp))
        assert st2.items == [] and st2.missing == {}
    src = inspect.getsource(dl._lifecycle)
    assert "if st.strict:" in src and "({} if st.strict else scen).items()" in src
    sig = inspect.signature(dl.lifecycle_check)
    assert sig.parameters["strict"].default is False

def slot_factory(tmp: Path, answers, repair_ok=True, protected=None, extent=None):
    import main as m
    from agent import seed_foundation as sf

    f = factory(tmp / "app")
    f.seed_inventory = sf.inventory(TREE, ["REQ-1"])
    calls = {"repair": [], "restore": 0, "checks": 0, "e2e": [], "extent": 0}
    queue = list(answers)

    def names(inv):
        calls["checks"] += 1
        return queue.pop(0) if queue else ([], {"completed": True}, [])
    f.seed_foundation_names = names

    def repair(label, check, text, test_files=None, deadline=None, keep_if=None):
        calls["repair"].append((label, deadline, text))
        outcome = check()
        calls["outcome"] = outcome
        kept = bool(repair_ok and (outcome.ok or (keep_if is not None and keep_if())))
        if not kept:
            calls["restore"] += 1
        return kept
    f.repair_round = repair
    f.snapshot_sources = lambda: {"a": b"1"}

    def restore(snapshot):
        calls["restore"] += 1
        return 1
    f.restore_sources = restore
    f.hard_fits = lambda *a, **k: True
    f.protected_specs = lambda: ["backend/test-e2e/REQ-1.spec.js"] if protected is not None else []
    f.foundation_run_list = lambda *a, **k: ["backend/test-e2e/REQ-1.spec.js"]
    f.continuation = {"baseline": "taken", "baseline_red": {}}

    def ext(end):
        calls["extent"] += 1
        return extent or {"status": "clean", "result": "clean"}
    f.seed_foundation_extent = ext
    p = Patch()
    p.set(m, "run_e2e", lambda project_dir, specs, log=print, **k: (calls["e2e"].append(specs) or protected))
    return f, calls, p

@case
def the_foundation_slot_keeps_progress_and_rolls_back_otherwise():
    import main as m

    full = {"completed": True, "missing": {"s1": ["EVO-M01-RENAME-OK"], "s2": ["EVO-M01-RENAME-DUP"]}}
    with tempfile.TemporaryDirectory() as tmp:

        f, calls, p = slot_factory(Path(tmp), [([], {"completed": True}, [])])
        with p, Logs() as lines:
            f.evolution_seed_foundation_slot()
            f.evolution_seed_foundation_slot()
        assert calls["repair"] == [] and calls["checks"] == 1 and f.evolution["health"]["seed_foundation"]["status"] == "clean"
        assert any("[seed-foundation] kept: nothing to add" in l for l in lines), lines
    with tempfile.TemporaryDirectory() as tmp:

        f, calls, p = slot_factory(Path(tmp), [(["EVO-M01-RENAME-OK", "EVO-M01-RENAME-DUP"], full, []), ([], {"completed": True}, [])],
                                   protected=m.StepResult(True, "1 passed"))
        with p, Logs() as lines:
            f.evolution_seed_foundation_slot()
            f.evolution_seed_foundation_slot()
        assert len(calls["repair"]) == 1 and calls["restore"] == 0 and calls["e2e"] == [["backend/test-e2e/REQ-1.spec.js"]]
        label, deadline, text = calls["repair"][0]
        assert label == "seed-foundation" and "`EVO-M01-RENAME-OK`, `EVO-M01-RENAME-DUP`" in text
        assert "worksheet grid must render" not in text and calls["extent"] == 0
        book = f.evolution["health"]["seed_foundation"]
        assert book["status"] == "kept" and book["missing_after"] == [] and book["missing_before"] == 2, book
        assert any(l.startswith("[seed-foundation] kept: 2 of 2 missing name(s) added") for l in lines), lines
    with tempfile.TemporaryDirectory() as tmp:

        f, calls, p = slot_factory(Path(tmp), [(["A", "B"], full, []), (["A", "B"], full, [])])
        with p, Logs() as lines:
            f.evolution_seed_foundation_slot()
        assert f.evolution["health"]["seed_foundation"]["status"] == "rolled back" and "still missing (2)" in calls["outcome"].output
    with tempfile.TemporaryDirectory() as tmp:

        f, calls, p = slot_factory(Path(tmp), [(["A", "B"], full, []), (["C"], full, [{"check": "data-lifecycle/upgrade_failed", "problem": "boom"}])])
        with p, Logs():
            f.evolution_seed_foundation_slot()
        out = calls["outcome"].output
        assert not calls["outcome"].ok and "now missing: `C`" in out and "upgrade_failed" in out, out
    with tempfile.TemporaryDirectory() as tmp:

        red = m.StepResult(False, "  1) REQ-1.spec.js:3:1 › signs in\n  1 failed")
        f, calls, p = slot_factory(Path(tmp), [(["A"], full, []), ([], {"completed": True}, [])], protected=red)
        with p, Logs():
            f.evolution_seed_foundation_slot()
        assert calls["restore"] == 1 and f.evolution["health"]["seed_foundation"]["status"] == "rolled back"
    with tempfile.TemporaryDirectory() as tmp:

        f, calls, p = slot_factory(Path(tmp), [(["A", "B"], full, []), (["A"], {"completed": True, "misplaced": {"B": "evo_code"}}, [])])
        with p, Logs():
            f.evolution_seed_foundation_slot()
        assert not calls["outcome"].ok and "under another display name" in calls["outcome"].output
    with tempfile.TemporaryDirectory() as tmp:

        f, calls, p = slot_factory(Path(tmp), [([], {"completed": False, "notes": ["deadline"]}, [])])
        with p, Logs():
            f.evolution_seed_foundation_slot()
        assert calls["repair"] == [] and f.evolution["health"]["seed_foundation"]["status"].startswith("not run")

@case
def the_slot_asks_for_the_grid_extent_only_when_the_start_up_probe_found_it_short():

    with settings_with(seed_foundation_staged=False):
        one_task_grid_path()

def one_task_grid_path():
    with tempfile.TemporaryDirectory() as tmp:
        f, calls, p = slot_factory(Path(tmp), [([], {"completed": True}, []), ([], {"completed": True}, [])],
                                   extent={"status": "findings", "result": "renders only to J20"})
        f.evolution["health"] = {"health_check": [{"item": "extent", "status": "findings", "result": "grid renders only up to J/20"}]}
        with p, Logs():
            f.evolution_seed_foundation_slot()
        assert len(calls["repair"]) == 1 and "up to L40" in calls["repair"][0][2] and "grid renders only up to J/20" in calls["repair"][0][2]
        assert calls["extent"] == 1 and not calls["outcome"].ok and "grid extent" in calls["outcome"].output
    with tempfile.TemporaryDirectory() as tmp:
        f, calls, p = slot_factory(Path(tmp), [([], {"completed": True}, []), ([], {"completed": True}, [])],
                                   extent={"status": "not run", "result": "no browser"})
        f.evolution["health"] = {"health_check": [{"item": "extent", "status": "findings", "result": "short"}]}
        with p, Logs():
            f.evolution_seed_foundation_slot()
        assert calls["outcome"].ok and f.evolution["health"]["seed_foundation"]["status"] == "kept"

@case
def the_switch_off_is_v161():
    import main as m
    from agent import data_lifecycle as dl

    with tempfile.TemporaryDirectory() as tmp:
        f = factory(Path(tmp) / "app")
        with settings_with(evolution_seed_foundation=False), Logs():
            assert f.seed_foundation_inventory(TREE) is None and "seed_inventory" not in f.__dict__
            seen = []
            p = Patch()
            p.set(dl, "lifecycle_check", lambda *a, **k: (seen.append(k) or ([], {"paths": {}})))
            f.hard_fits = lambda *a, **k: True
            f.handoff_tree = TREE
            with p:
                f.evolution_lifecycle_run("batch X", ["REQ-1"])
            assert seen and "strict" not in seen[0], seen
            assert seen[0]["scenario_names"] == m.evolution.scenario_seed_names(TREE, ["REQ-1"])
            f.evolution["health"] = {"seconds": 10.0}
            f.seed_inventory = {"names": ["x"]}
            assert f.evolution_extra_budget([{"id": "A"}]) == 10.0
            calls = []
            f.repair_round = lambda *a, **k: calls.append(a)
            f.evolution_seed_foundation_slot()
            assert calls == [] and "seed_foundation" not in f.evolution["health"]

        f2 = factory(Path(tmp) / "app2")
        f2.hard_fits = lambda *a, **k: True
        f2.handoff_tree = TREE
        seen = []
        p = Patch()
        p.set(dl, "lifecycle_check", lambda *a, **k: (seen.append(k) or ([], {"paths": {}})))
        with p, Logs() as lines:
            f2.evolution_lifecycle_run("batch X", ["REQ-1"])
        assert seen[0]["strict"] is True and seen[0]["scenario_names"]["REQ-1 - duplicate"][-1] == "EVO-M01-ARCHIVE-RESERVED"
        assert any(l.startswith("[seed-foundation] 2 scenario(s), 4 record name(s)") for l in lines), lines
        f2.evolution["health"] = {"seconds": 10.0}
        assert f2.evolution_extra_budget([{"id": "A"}]) == 10.0 + 1500

@case
def the_wiring_sits_where_it_should():
    import main as m

    queue = inspect.getsource(m.Factory.run_queue)
    a = queue.index("self.evolution_seed_foundation_slot()")
    b = queue.index("self.evolution_entry_debt_slot(node)")
    c = queue.index("ok = self.attempt_node(")
    assert a < b < c
    assert "not requeued and not done_ids and setting_on(\"evolution_seed_foundation\")" in queue
    run = inspect.getsource(m.Factory.run)
    i = run.index("evo_inventory = self.seed_foundation_inventory(tree) if evo else None")
    j = run.index("seed_foundation.batch_block(evo_inventory")
    k = run.index("extra = self.evolution_extra_budget(work) if evo else 0.0")
    assert i < j < k
    findings = inspect.getsource(m.Factory.evolution_lifecycle_findings)
    assert "seed_foundation.scenario_names(inv, members)" in findings

def main() -> int:
    failed = 0
    skipped = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception as exc:
            import unittest
            if isinstance(exc, unittest.SkipTest):
                skipped += 1
                print(f"SKIP  {function.__name__}: {exc}")
                continue
            failed += 1
            traceback.print_exc()
            print(f"FAIL  {function.__name__}")
    print(f"{len(CASES) - failed - skipped}/{len(CASES)} passed" + (f", {skipped} skipped" if skipped else ""))
    return 1 if failed else 0

if __name__ == "__main__":
    os.chdir(ROOT)
    sys.exit(main())
