from __future__ import annotations

import ast
import inspect
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path.cwd() if (Path.cwd() / "main.py").is_file() else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES = []

def case(function):
    CASES.append(function)
    return function

def fresh():
    from agent import fsutil, watchdog

    watchdog._ACTIVE = None
    fsutil.STOPPING.clear()

def fake_dog():
    from agent.watchdog import Watchdog

    return Watchdog(83700.0, started_mono=time.monotonic(), started_wall=time.time(), log=lambda line: None,
                    exit=lambda code: None, wait=lambda seconds: False, stop_children=lambda log=print: 0)

def factory(root: Path):
    import main as m

    f = m.Factory.__new__(m.Factory)
    f.project_dir, f.restore_failures = root, 0
    return f

def tree(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

def main_source() -> str:
    return (ROOT / "main.py").read_text(encoding="utf-8")

@case
def main_first_statement_is_a_try_arming_the_watchdog_early():
    module = ast.parse(main_source())
    fn = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    body = [node for node in fn.body if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))]
    first = body[0]
    assert isinstance(first, ast.Try), ast.dump(first)[:200]
    calls = [ast.unparse(node.func) for node in ast.walk(ast.Module(body=first.body, type_ignores=[])) if isinstance(node, ast.Call)]
    assert "watchdog.arm_early" in calls, calls
    assert first.handlers and all(h.type is None or ast.unparse(h.type) in ("Exception", "BaseException") for h in first.handlers)

class Sentinel(Exception):
    pass

def run_main_with(arm_early):
    import main as m

    order: list[str] = []
    had = hasattr(m.watchdog, "arm_early")
    saved_arm, saved_parse = getattr(m.watchdog, "arm_early", None), m.parse_args

    def parse():
        order.append("parse_args")
        raise Sentinel()

    m.watchdog.arm_early = lambda **kw: (order.append("arm_early"), arm_early(**kw))[1]
    m.parse_args = parse
    try:
        try:
            m.main()
        except Sentinel:
            pass
        else:
            raise AssertionError("parse_args was never reached")
    finally:
        m.parse_args = saved_parse
        if had:
            m.watchdog.arm_early = saved_arm
        else:
            del m.watchdog.arm_early
    return order

@case
def main_calls_arm_early_before_parse_args_with_the_process_start():
    import main as m

    seen: dict = {}
    order = run_main_with(lambda **kw: seen.update(kw))
    assert order == ["arm_early", "parse_args"], order
    assert seen == {"started_mono": m.MONO_STARTED, "started_wall": m.PROCESS_STARTED}, seen

@case
def an_arm_early_that_raises_does_not_stop_main():
    def broken(**kw):
        raise RuntimeError("no timer")

    order = run_main_with(broken)
    assert order == ["arm_early", "parse_args"], order

@case
def run_sets_the_global_deadline_again_after_loading_the_checkpoint():
    import main as m

    source = inspect.getsource(m.Factory.run)
    load = source.index("self.load_checkpoint(resume_digest)")
    first = source.index("set_global_deadline(self.hard_end())")
    assert first < load
    assert source.find("set_global_deadline(self.hard_end())", load) > load, "no set_global_deadline after load_checkpoint"

@case
def the_lint_wrapper_passes_the_outcome_infra_on():
    source = main_source()
    at = source.index('linted = TestOutcome(False, "APPLICATION LINT FAILED')
    end = source.index("\n", source.index(")", source.index("outcome.output", at)) + 1)
    call = source[at:source.index("\n", end + 1)]
    assert 'infra=getattr(outcome, "infra", "")' in call, call

SNAP = {"frontend/src/App.tsx": "kept app", "backend/src/server.js": "kept server"}

@case
def restore_puts_content_back_and_deletes_an_added_file():
    fresh()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tree(root, SNAP)
        f = factory(root)
        snapshot = f.snapshot_sources()
        tree(root, {"frontend/src/App.tsx": "broken app", "frontend/src/helper.ts": "new helper"})
        changed = f.restore_sources(snapshot)
        assert changed == 2, changed
        assert f.snapshot_sources() == snapshot
        assert not (root / "frontend/src/helper.ts").exists()
        assert f.restore_failures == 0

@case
def restore_writes_every_kept_file_before_any_delete():
    fresh()
    import main as m

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tree(root, SNAP)
        f = factory(root)
        snapshot = f.snapshot_sources()
        tree(root, {"frontend/src/App.tsx": "broken", "backend/src/server.js": "broken", "frontend/src/a.ts": "added",
                    "backend/src/z.js": "added"})
        calls: list = []
        import os
        real_replace, real_unlink = os.replace, Path.unlink

        def record_replace(src, dst):
            calls.append((Path(dst).name, False))
            return real_replace(src, dst)

        def record_unlink(self, *args, **kwargs):
            if not self.name.endswith(m.source_io.TEMP_SUFFIX):
                calls.append((self.name, True))
            return real_unlink(self, *args, **kwargs)

        os.replace, Path.unlink = record_replace, record_unlink
        try:
            f.restore_sources(snapshot)
        finally:
            os.replace, Path.unlink = real_replace, real_unlink
        kinds = [deleted for _, deleted in calls]
        assert kinds == [False, False, True, True], calls
        assert f.snapshot_sources() == snapshot

@case
def restore_leaves_no_temporary_file_and_snapshots_skip_it():
    fresh()
    import main as m
    from agent import source_io

    assert source_io.TEMP_SUFFIX in m.Factory.SNAPSHOT_SKIP_SUFFIXES
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tree(root, SNAP)
        f = factory(root)
        snapshot = f.snapshot_sources()
        tree(root, {"frontend/src/App.tsx": "broken", "backend/src/server.js": "broken"})
        f.restore_sources(snapshot)
        assert not [p for p in root.rglob("*") if p.name.endswith(source_io.TEMP_SUFFIX)]
        tree(root, {"frontend/src/App.tsx" + source_io.TEMP_SUFFIX: "leftover"})
        assert f.snapshot_sources() == snapshot

@case
def codegen_roll_back_restores_originals_before_deleting_created_files():
    fresh()
    from agent import source_io
    from agent.codegen import CodegenImplementer

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tree(root, {"frontend/src/a_new.ts": "created", "frontend/src/b_old.ts": "changed", "frontend/src/c_old.ts": "same",
                    "frontend/src/z_new.ts": "created too"})
        undo = {root / "frontend/src/a_new.ts": None, root / "frontend/src/b_old.ts": b"old b",
                root / "frontend/src/z_new.ts": None, root / "frontend/src/c_old.ts": b"same"}
        calls: list = []
        real = source_io.put_back

        def record(path, content):
            calls.append((Path(path).name, content is None))
            return real(path, content)

        source_io.put_back = record
        try:
            CodegenImplementer._roll_back(SimpleNamespace(log=lambda *a: None), undo, "N", 1)
        finally:
            source_io.put_back = real
        kinds = [deleted for _, deleted in calls]
        assert kinds == [False, True, True], calls
        assert (root / "frontend/src/b_old.ts").read_bytes() == b"old b"
        assert (root / "frontend/src/c_old.ts").read_bytes() == b"same"
        assert not (root / "frontend/src/a_new.ts").exists() and not (root / "frontend/src/z_new.ts").exists()

def restore_point_world(refresh: bool) -> str:
    fresh()
    from agent import watchdog

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tree(root, {"frontend/src/App.tsx": "original"})
        f = factory(root)
        watchdog._ACTIVE = fake_dog()
        try:
            token = f.begin_restore_point("description pass")
            assert token is not None and watchdog._ACTIVE.current()[0] == "description pass"
            tree(root, {"frontend/src/App.tsx": "accepted repair", "frontend/src/added.ts": "kept helper"})
            if refresh:
                assert f.refresh_restore_point() is True
            tree(root, {"frontend/src/App.tsx": "half-done later repair"})
            watchdog._ACTIVE.current()[1]()
            text = (root / "frontend/src/App.tsx").read_text(encoding="utf-8")
            if refresh:
                assert (root / "frontend/src/added.ts").is_file()
            watchdog.end(token)
            return text
        finally:
            fresh()

@case
def a_refreshed_restore_point_puts_back_the_accepted_state():
    assert restore_point_world(refresh=True) == "accepted repair"

@case
def without_refresh_the_restore_point_is_the_state_before_the_pass():
    assert restore_point_world(refresh=False) == "original"

@case
def refresh_takes_an_explicit_snapshot():
    fresh()
    from agent import watchdog

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tree(root, {"frontend/src/App.tsx": "original"})
        f = factory(root)
        watchdog._ACTIVE = fake_dog()
        try:
            f.begin_restore_point("description pass")
            assert f.refresh_restore_point({"frontend/src/App.tsx": b"given"}) is True
            watchdog._ACTIVE.current()[1]()
            assert (root / "frontend/src/App.tsx").read_bytes() == b"given"
        finally:
            fresh()

@case
def without_a_watchdog_there_is_no_restore_point():
    fresh()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tree(root, {"frontend/src/App.tsx": "original"})
        f = factory(root)
        assert f.begin_restore_point("description pass") is None
        assert f.refresh_restore_point() is False
        g = factory(root)
        assert g.refresh_restore_point() is False

@case
def the_description_pass_refreshes_at_each_verified_state_and_main_wraps_it():

    source = (ROOT / "agent" / "description_pass.py").read_text(encoding="utf-8")
    lines = [line.strip() for line in source.splitlines()]
    sets = [i for i, line in enumerate(lines) if line == "self.last_good = f.snapshot_sources()"]
    assert len(sets) >= 2, sets
    for i in sets:
        assert lines[i + 1] == "self.refresh_restore_point()", (i, lines[i + 1])
    close = source.index("def close_leaf(")
    body = source[close:source.find("\n    def ", close + 10)]
    assert "self.last_good = f.snapshot_sources()\n        self.refresh_restore_point()" in body, "close_leaf does not refresh"
    run = inspect.getsource(__import__("main").Factory.run)
    wrap = run.index('self.begin_restore_point("description pass")')
    assert wrap < run.index("run_description_pass(self")
    assert 'begin_snapshot(self, "description pass")' not in run

@case
def description_pass_refresh_hands_last_good_to_the_factory():
    from agent.description_pass import DescriptionPass

    got: list = []
    good = {"frontend/src/App.tsx": b"good"}
    DescriptionPass.refresh_restore_point(SimpleNamespace(f=SimpleNamespace(refresh_restore_point=got.append),
                                                          last_good=good, log=lambda *a: None))
    assert got == [good], got
    DescriptionPass.refresh_restore_point(SimpleNamespace(f=SimpleNamespace(refresh_restore_point=got.append),
                                                          last_good=None, log=lambda *a: None))
    assert len(got) == 1

    def broken(snapshot):
        raise RuntimeError("no")

    lines: list = []
    DescriptionPass.refresh_restore_point(SimpleNamespace(f=SimpleNamespace(refresh_restore_point=broken),
                                                          last_good=good, log=lines.append))
    assert lines and "not refreshed" in lines[0]

def booked(run, specs=("backend/test-e2e/a.spec.js", "backend/test-e2e/b.spec.js")):
    import main as m

    f = m.Factory.__new__(m.Factory)
    f.foundation_covered = lambda spec: []
    f.covered_state = lambda files: {}
    state: dict = {}
    f.note_foundation_run(state, list(specs), run)
    return {Path(spec).name: not entry["red"] for spec, entry in state["foundation_seen"].items()}

@case
def a_not_run_spec_is_not_booked_green_on_an_ok_run():
    got = booked(SimpleNamespace(ok=True, output="NOT RUN SPEC FILES: b.spec.js", ran=3))
    assert got == {"a.spec.js": False, "b.spec.js": False} or got == {"a.spec.js": True, "b.spec.js": False}, got
    assert got["b.spec.js"] is False

@case
def a_not_run_spec_is_not_booked_green_when_another_spec_is_named_failed():
    got = booked(SimpleNamespace(ok=False, output="FAILED SPEC FILES: c.spec.js\nNOT RUN SPEC FILES: a.spec.js, b.spec.js", ran=3),
                 specs=("backend/test-e2e/a.spec.js", "backend/test-e2e/b.spec.js", "backend/test-e2e/d.spec.js"))
    assert got == {"a.spec.js": False, "b.spec.js": False, "d.spec.js": True}, got

@case
def an_ok_run_with_no_test_run_is_not_booked_green():
    got = booked(SimpleNamespace(ok=True, output="", ran=0))
    assert got == {"a.spec.js": False, "b.spec.js": False}, got
    got = booked(SimpleNamespace(ok=True, output="", ran=4))
    assert got == {"a.spec.js": True, "b.spec.js": True}, got

@case
def an_unnamed_regression_twice_is_inconclusive_and_not_ok():
    import main as m
    from agent.settings import SETTINGS
    from agent.verify import StepResult

    saved_setting, saved_run, saved_log = SETTINGS.get("timesave_unnamed_inconclusive"), m.run_e2e, m.log
    runs: list = []

    def second(project_dir, specs, log=print):
        runs.append(list(specs))
        return StepResult(False, "backend did not become ready for e2e")

    SETTINGS["timesave_unnamed_inconclusive"] = True
    m.run_e2e, m.log = second, (lambda *a, **k: None)
    try:
        f = m.Factory.__new__(m.Factory)
        f.project_dir = Path(tempfile.gettempdir())
        f.record_run = lambda *a, **k: None
        first = StepResult(False, "the server exited before GET / answered")
        result, inconclusive = f.settle_unnamed_regression("REQ-9", ["backend/test-e2e/a.spec.js"], first)
        assert inconclusive is True and result.ok is False, (result, inconclusive)
        assert runs == [["backend/test-e2e/a.spec.js"]], runs
        named = StepResult(False, "FAILED SPEC FILES: a.spec.js")
        m.run_e2e = lambda project_dir, specs, log=print: named
        result, inconclusive = f.settle_unnamed_regression("REQ-9", ["backend/test-e2e/a.spec.js"], first)
        assert inconclusive is False and result is named
    finally:
        SETTINGS["timesave_unnamed_inconclusive"] = saved_setting
        m.run_e2e, m.log = saved_run, saved_log

@case
def an_inconclusive_regression_run_is_red_but_no_foundation_break():
    source = inspect.getsource(__import__("main").Factory.run_checks) if hasattr(__import__("main").Factory, "run_checks") else main_source()
    line = 'regression_state["foundation"] = bool(failed_names & foundation) or (not failed_names and not inconclusive)'
    assert line in source
    at = source.index(line)
    assert source.rfind('regression_state["red"] = True', 0, at) > source.rfind("if not again.ok", 0, at) > 0

@case
def a_date_like_name_is_not_masked_but_a_timestamp_name_is():
    import main as m

    assert m.error_signature("Error: missing invoice-20260930-east") != m.error_signature("Error: missing payment-20260930-west")
    assert m.error_signature("Error: expected branch pw-branch-1790680142446-t5ar to be listed") == \
        m.error_signature("Error: expected branch pw-branch-1790682839944-qxq5 to be listed")

INIT_DB = "backend/src/database/init_db.js"
APP = "frontend/src/App.tsx"
UNRELATED = [{"where": "list page", "requirement": "the list shows the title", "problem": "title missing"}]

def seed_guard_world(findings, recheck_passed: bool = True, change_seed: bool = True):
    import main as m
    from agent.codegen import ImplementResult
    from agent.testrun import TestOutcome

    saved_log = m.log
    m.log = lambda *a, **k: None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tree(root, {INIT_DB: "seed before", APP: "app before"})
            f = factory(root)
            before = f.snapshot_sources()
            tree(root, {APP: "app repaired"})
            if change_seed:
                tree(root, {INIT_DB: "seed rewritten", "backend/src/database/seeds/seed_more.js": "added seed"})
            checks: list = []

            def check():
                checks.append(f.snapshot_sources())
                return TestOutcome(recheck_passed, "recheck output")

            fix = ImplementResult("passed", [INIT_DB, APP, "backend/src/database/seeds/seed_more.js"], last_check="green before")
            out = f.audit_seed_guard("REQ-1", before, findings, fix, check)
            now = f.snapshot_sources()
            return SimpleNamespace(fix=fix, out=out, checks=checks, now=now)
    finally:
        m.log = saved_log

@case
def an_unrelated_seed_rewrite_is_put_back_and_the_rest_kept_when_green():
    w = seed_guard_world(UNRELATED)
    assert len(w.checks) == 1, w.checks
    assert w.checks[0][INIT_DB] == b"seed before" and "backend/src/database/seeds/seed_more.js" not in w.checks[0]
    assert w.now[INIT_DB] == b"seed before" and w.now[APP] == b"app repaired"
    assert w.out is w.fix and w.out.status == "passed" and w.out.written == [APP], w.out
    assert w.out.last_check == "recheck output"

@case
def a_seed_related_finding_keeps_the_seed_change_unchecked():
    for findings in ([{"where": "seed data", "requirement": "", "problem": ""}],
                     [{"where": "list", "requirement": "the SEED has three books", "problem": ""}],
                     [{"where": "list", "requirement": "", "problem": "种子数据缺少记录"}]):
        w = seed_guard_world(findings)
        assert w.checks == [] and w.out is w.fix and w.now[INIT_DB] == b"seed rewritten", findings
        assert INIT_DB in w.out.written

@case
def a_repair_red_without_its_seed_change_gives_up():
    w = seed_guard_world(UNRELATED, recheck_passed=False)
    assert len(w.checks) == 1
    assert w.out.status == "gave_up", w.out
    assert w.fix.status == "passed"

@case
def a_repair_that_left_the_seed_alone_is_not_checked_again():
    w = seed_guard_world(UNRELATED, change_seed=False)
    assert w.checks == [] and w.out is w.fix and w.out.status == "passed"

INIT_BEFORE = "\n".join(["db.exec(`CREATE TABLE IF NOT EXISTS notes (", "  id INTEGER PRIMARY KEY,", "  title TEXT NOT NULL", ");`);",
                         "const rows = [", '  { title: "First" },', "];"])
INIT_AFTER = "\n".join(["db.exec(`CREATE TABLE IF NOT EXISTS notes (", "  id INTEGER PRIMARY KEY,", "  priority INTEGER NOT NULL DEFAULT 0,",
                        "  title TEXT NOT NULL", ");`);", "const rows = [", '  { title: "First", priority: 2 },', "];"])

@case
def a_schema_change_in_init_db_is_not_put_back():
    import main as m
    from agent.codegen import ImplementResult

    saved_log = m.log
    m.log = lambda *a, **k: None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tree(root, {INIT_DB: INIT_BEFORE, APP: "app before"})
            f = factory(root)
            before = f.snapshot_sources()
            tree(root, {INIT_DB: INIT_AFTER, APP: "app repaired"})
            checks: list = []
            fix = ImplementResult("passed", [INIT_DB, APP], last_check="green before")
            out = f.audit_seed_guard("REQ-1", before, UNRELATED, fix, lambda: checks.append(1))
            assert out is fix and out.status == "passed" and out.written == [INIT_DB, APP], out
            assert checks == []
            assert (root / INIT_DB).read_text(encoding="utf-8") == INIT_AFTER
    finally:
        m.log = saved_log

@case
def schema_line_matches_ddl_and_typed_columns_not_seed_rows():
    import main as m

    line = m.Factory.SCHEMA_LINE
    for text in ("  priority INTEGER NOT NULL DEFAULT 0,", "CREATE TABLE IF NOT EXISTS tags (", "alter table notes add column x",
                 "DROP INDEX idx_notes", "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP"):
        assert line.search(text), text
    for text in ('{ title: "Delete me", trashed: 0 },', "insert into notes values ('some text')",
                 "  { title: 'Integer maths', body: 'real text' },"):
        assert not line.search(text), text

@case
def the_seed_guard_runs_on_a_passed_audited_repair():
    import main as m

    source = main_source()
    at = source.index("self.audit_seed_guard(req_id, before_audit, audit_findings, fix, node_check)")
    assert source.rfind('if fix.status == "passed":', 0, at) > source.rfind("before_audit = self.snapshot_sources()", 0, at) > 0
    assert source.index('if fix.status == "passed":', at) - at < 200
    assert m.Factory.SEED_FILE.match("backend/src/database/init_db.js") and m.Factory.SEED_FILE.match("backend/src/database/seeds/seed_books.ts")
    assert not m.Factory.SEED_FILE.match("backend/src/routes/seed.js") and not m.Factory.SEED_FILE.match("frontend/src/database/init_db.js")

def main() -> int:
    if not __debug__:
        print("FAIL  self-tests need assertions: run without -O and without PYTHONOPTIMIZE")
        return 1
    started = time.time()
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=6)
        finally:
            fresh()
    print(f"{len(CASES) - failed}/{len(CASES)} v144a cases passed in {time.time() - started:.1f}s")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
