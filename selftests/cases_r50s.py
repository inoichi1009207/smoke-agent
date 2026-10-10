from __future__ import annotations

import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cases_engine142 as e142
import cases_p141s3 as s3
import cases_rollback as rb
import cases_v144a as va
import cases_v144m as vm

NL = chr(10)
CASES = []
F3, F4 = s3.F3, s3.F4
A_BREAK = rb.brk("REQ-1-2: Scenario 1")
B_BREAK = s3.F4_BREAK
MARK = {F3: A_BREAK, F4: B_BREAK}

def case(function):
    CASES.append(function)
    return function

def repairer(seen: list):
    from agent.codegen import ImplementResult

    class Repairer:
        def __init__(self, llm, project_dir, check, **options):
            self.project_dir, self.check = Path(project_dir), check

        def implement(self, node, tests, outcome, extra_context=""):
            spec = next(s for s in (F3, F4) if Path(s).name[:-8] == node.get("id"))
            app = self.project_dir / rb.APP
            app.write_text(app.read_text(encoding="utf-8").replace(MARK[spec], ""), encoding="utf-8")
            verdict = self.check()
            seen.append((Path(spec).name, verdict.passed, verdict.output[:200]))
            return ImplementResult("passed" if verdict.passed else "budget", [rb.APP], "scripted", 1)

    return Repairer

def two_red_candidates(root: Path, green_order: list[str]):
    f = vm.closing_factory(root, "APP " + A_BREAK + " " + B_BREAK)
    f.e2e_specs = [rb.F1, rb.F2, F3, F4]
    (root / F3).write_text("test('x', async () => {});" + NL, encoding="utf-8")
    f.spec_context[F3] = {"node": {"id": "REQ-1-2"}, "contract": "", "relevant": [], "tests": {}, "written": [], "members": []}
    f.green_specs = list(green_order)
    f.file_specs = {rb.APP: [rb.F1]}
    f.debt_specs = {}
    f.unknown_debts = {"REQ-9": [F3, F4]}
    vm.measured(f)
    return f

@case
def confirmed_red_unknown_candidates_do_not_undo_each_others_repair():
    for order in ([rb.F1, rb.F2, F3, F4], [rb.F1, rb.F2, F4, F3], [F4, F3, rb.F1, rb.F2]):
        lines: list[str] = []
        seen: list = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = two_red_candidates(root, order)
            world = rb.World(root)
            with vm.Patched([], [], lines):
                import main as m

                m.run_e2e = lambda project_dir, specs, log=print, **kw: world.run_e2e(project_dir, specs, log=log)
                m.Implementer = repairer(seen)
                repaired = f.repair_debt_specs()
            app = (root / rb.APP).read_text(encoding="utf-8")
            assert [(name, ok) for name, ok, _ in seen] == [("REQ-1-2.spec.js", True), ("REQ-4-2-3.spec.js", True)], (order, seen, lines)
            assert repaired == 2 and A_BREAK not in app and B_BREAK not in app, (order, repaired, app, lines)
            assert f.debt_tried == [], f.debt_tried

            a_fixed = "APP  " + B_BREAK
            assert not [specs for specs in world.runs_on(a_fixed) if F4 in specs and F3 not in specs and len(specs) > 1], world.runs

            done = "APP  "
            assert any(F3 in specs for specs in world.runs_on(done)), (world.runs, done)

@case
def a_repair_that_breaks_the_repaired_candidate_is_still_put_back():

    from agent.codegen import ImplementResult

    seen: list = []

    class Breaker:
        def __init__(self, llm, project_dir, check, **options):
            self.project_dir, self.check = Path(project_dir), check

        def implement(self, node, tests, outcome, extra_context=""):
            app = self.project_dir / rb.APP
            text = app.read_text(encoding="utf-8")
            if node.get("id") == "REQ-1-2":
                app.write_text(text.replace(A_BREAK, ""), encoding="utf-8")
            else:
                app.write_text(text.replace(B_BREAK, "") + " " + A_BREAK, encoding="utf-8")
            verdict = self.check()
            seen.append((node.get("id"), verdict.passed))
            return ImplementResult("passed" if verdict.passed else "budget", [rb.APP], "scripted", 1)

    lines: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = two_red_candidates(root, [rb.F1, rb.F2, F3, F4])
        with vm.Patched([], [], lines):
            import main as m

            m.Implementer = Breaker
            repaired = f.repair_debt_specs()
        app = (root / rb.APP).read_text(encoding="utf-8")
        assert seen == [("REQ-1-2", True), ("REQ-4-2-3", False)], (seen, lines)
        assert repaired == 1 and A_BREAK not in app and B_BREAK in app, (repaired, app)

@case
def an_incomplete_put_back_says_so_in_its_result_and_in_the_watchdog_line():
    import main as m

    va.fresh()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        va.tree(root, va.SNAP)
        f = va.factory(root)
        snapshot = f.snapshot_sources()
        va.tree(root, {"frontend/src/App.tsx": "broken app"})
        real = m.source_io.put_back_all
        m.source_io.put_back_all = lambda items, late_deletes=None: (0, [])
        saved_log, m.log = m.log, (lambda line: None)
        try:
            result = f.restore_sources(snapshot)
            lines: list[str] = []
            dog = va.fake_dog()
            dog.log = lines.append
            dog._restore("closing repair of REQ-1-2.spec.js", lambda: f.restore_sources(snapshot))
        finally:
            m.source_io.put_back_all, m.log = real, saved_log
        assert result == 0 and result.complete is False and isinstance(result, int), result
        assert "INCOMPLETE" in f"{result}" and f.restore_failures == 2, (f"{result}", f.restore_failures)
        assert lines and "INCOMPLETE" in lines[-1], lines

        done = f.restore_sources(snapshot)
        assert done == 1 and done.complete is True and f"{done}" == "1" and str(done) == "1", f"{done}"
        assert f.snapshot_sources() == snapshot

@case
def the_files_put_back_all_could_not_put_back_are_named():
    import main as m

    va.fresh()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        va.tree(root, va.SNAP)
        f = va.factory(root)
        snapshot = f.snapshot_sources()
        va.tree(root, {"frontend/src/App.tsx": "broken app"})
        real = m.source_io.put_back_all
        m.source_io.put_back_all = lambda items, late_deletes=None: (0, [root / "frontend/src/App.tsx"])
        lines: list[str] = []
        saved_log, m.log = m.log, lines.append
        try:
            f.restore_sources(snapshot)
        finally:
            m.source_io.put_back_all, m.log = real, saved_log
        named = [l for l in lines if "could not be put back" in l]
        assert len(named) == 1 and "frontend/src/App.tsx" in named[0], lines

@case
def late_deletes_list_names_without_reading_contents_under_the_lock():
    import main as m

    va.fresh()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        va.tree(root, va.SNAP)
        f = va.factory(root)
        snapshot = f.snapshot_sources()
        assert f.source_names() == set(snapshot)
        va.tree(root, {"frontend/src/App.tsx": "broken app"})
        real = m.source_io.put_back_all
        reads_in_late: list[int] = []

        def spy(items, late_deletes=None):
            counted: list[str] = []
            saved_read = Path.read_bytes

            def counting(self):
                counted.append(self.name)
                return saved_read(self)

            Path.read_bytes = counting
            try:
                late = list(late_deletes()) if late_deletes else []
            finally:
                Path.read_bytes = saved_read
            reads_in_late.append(len(counted))
            va.tree(root, {"frontend/src/late.ts": "late"})
            return real(items, late_deletes=late_deletes)

        m.source_io.put_back_all = spy
        saved_log, m.log = m.log, (lambda line: None)
        try:
            done = f.restore_sources(snapshot)
        finally:
            m.source_io.put_back_all, m.log = real, saved_log
        assert reads_in_late == [0], reads_in_late
        assert done.complete and f.snapshot_sources() == snapshot and not (root / "frontend/src/late.ts").exists()

def muted_tree(root: Path, tests: int = 30, chain: int = 50) -> list[str]:
    (root / "backend" / "tests").mkdir(parents=True, exist_ok=True)
    (root / "backend" / "src" / "lib").mkdir(parents=True, exist_ok=True)
    (root / "backend" / "src" / "app.js").write_text("const m = require('./lib/m0');" + NL, encoding="utf-8")
    for i in range(chain):
        nxt = f"require('./m{i + 1}');" if i + 1 < chain else "// end"
        (root / "backend" / "src" / "lib" / f"m{i}.js").write_text(nxt + NL, encoding="utf-8")
    files = []
    for i in range(tests):
        rel = f"backend/tests/t{i}.test.js"
        (root / rel).write_text("const app = require('../src/app');" + NL, encoding="utf-8")
        files.append(rel)
    return files

def muted_check(tmp: str, reads_max=None, seconds=None):
    import main as m

    root = Path(tmp)
    f = rb.make_factory(root, "APP")
    f.large_tree = True
    files = muted_tree(root)
    f.api_red_streaks = {p: f.API_MUTE_REDS for p in files}
    f.api_muted_at = {p: "stale" for p in files}
    if reads_max is not None:
        f.API_RECHECK_READS_MAX = reads_max
    if seconds is not None:
        f.API_RECHECK_SEC = seconds
    reads: list[str] = []
    real = Path.read_bytes
    lines: list[str] = []
    saved = m.log

    def counting(self):
        reads.append(self.name)
        return real(self)

    Path.read_bytes, m.log = counting, lines.append
    try:
        with e142.Settings(timesave_api_mute=True):
            kept = f.advisory_api_files("REQ-9", files, [])
    finally:
        Path.read_bytes, m.log = real, saved
    return f, files, kept, reads, lines

@case
def the_muted_files_recheck_reads_each_shared_import_once_per_check():
    with tempfile.TemporaryDirectory() as tmp:
        f, files, kept, reads, lines = muted_check(tmp)
        assert kept == files, kept
        assert len(reads) <= 30 + 51, len(reads)
        assert not any("stopped at its budget" in l for l in lines), lines

@case
def past_the_read_budget_the_remaining_muted_files_stay_muted_with_one_line():
    with tempfile.TemporaryDirectory() as tmp:
        f, files, kept, reads, lines = muted_check(tmp, reads_max=60)
        assert len(reads) <= 60, len(reads)
        assert kept and len(kept) < len(files), kept
        unchecked = [p for p in files if p not in kept]
        assert all(f.api_muted_at[p] == "stale" for p in unchecked), f.api_muted_at
        said = [l for l in lines if "stopped at its budget" in l]
        assert len(said) == 1 and f"{len(unchecked)} stay muted" in said[0], lines

@case
def past_the_seconds_budget_every_muted_file_stays_muted():
    with tempfile.TemporaryDirectory() as tmp:
        f, files, kept, reads, lines = muted_check(tmp, seconds=0.0)
        assert kept == [] and not reads, (kept, reads)
        assert any("stopped at its budget" in l and "30 stay muted" in l for l in lines), lines

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
    print(f"{len(CASES) - failed}/{len(CASES)} r50s cases passed in {time.time() - started:.1f}s")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
