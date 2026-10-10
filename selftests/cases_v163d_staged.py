from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.dont_write_bytecode = True

from cases_v162a_seed import TREE, Logs, Patch, factory, settings_with

CASES = []

def case(function):
    CASES.append(function)
    return function

SEED = "backend/src/database/seed_evo.js"
GRID = "frontend/src/components/Grid.tsx"
SPECS = ["backend/test-e2e/A-tabs.spec.js", "backend/test-e2e/B-cells.spec.js", "backend/test-e2e/C-insert.spec.js",
         "backend/test-e2e/D-plain.spec.js"]
SPEC_SOURCES = {
    SPECS[0]: "await page.getByRole('tab', { name: 'Sheet1' }).click();\n",
    SPECS[1]: "await expect(page.getByRole('gridcell', { name: 'A1' })).toBeVisible();\n",
    SPECS[2]: "await page.getByRole('menuitem', { name: 'Insert 1 row above' }).click();\n",
    SPECS[3]: "await expect(page).toHaveTitle('Ledger');\n",
}
ORIGINAL = {GRID: b"const N = 10;\n", "backend/src/database/init_db.js": b"init\n"}
NOW = 1000.0

def reference_slot():
    import main as m

    text = (Path(__file__).resolve().parent / "ref_v163_seed_slot.txt").read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.startswith("#"))
    import textwrap

    code = textwrap.dedent(code).replace("def evolution_seed_foundation_slot(", "def _ref_v163_seed_foundation_slot(", 1)
    exec(compile(code, "ref_v163_seed_slot.txt", "exec"), m.__dict__)
    return m.__dict__.pop("_ref_v163_seed_foundation_slot")

class World:

    def __init__(self, tmp: Path, edits, extent_status="findings", inv_extent=True, protected=True, raise_on=None):
        import main as m
        from agent import seed_foundation as sf

        self.m = m
        self.files = dict(ORIGINAL)
        self.edits = list(edits)
        self.seq: list[tuple] = []
        self.reads = 0
        self.raise_on = raise_on
        f = factory(tmp / "app")
        for rel, src in SPEC_SOURCES.items():
            (f.project_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            (f.project_dir / rel).write_text(src, encoding="utf-8")
        inv = sf.inventory(TREE, ["REQ-1"])
        if not inv_extent:
            inv["extent"] = {"grid": False, "col": 0, "row": 0, "cell": ""}
        f.seed_inventory = inv
        f.evolution["health"] = {"health_check": [{"item": "extent", "status": extent_status,
                                                   "result": "grid renders only up to the tenth column / row 20"}]}
        f.continuation = {"baseline": "taken", "baseline_red": {}, "protected": list(SPECS) if protected else []}
        f.green_specs = list(SPECS) if protected else []
        f.snapshot_sources = self.snapshot
        f.restore_sources = self.restore
        f.repair_round = self.repair
        f.seed_foundation_names = self.names
        f.seed_foundation_extent = self.extent
        f.hard_fits = lambda what, worst, *a, **k: (self.seq.append(("hard_fits", worst)) or True)
        f.foundation_run_list = lambda own=(), budget=0, what="": (self.seq.append(("run_list", tuple(own), what))
                                                                    or [s for s in f.protected_specs() if s not in own])
        self.f = f
        self.patch = Patch()
        self.patch.set(m, "run_e2e", self.e2e)
        self.patch.set(m.clock, "now", lambda: NOW)

    def snapshot(self):
        self.reads += 1
        return dict(self.files)

    def restore(self, snap):
        changed = sorted(rel for rel in set(snap) | set(self.files) if snap.get(rel) != self.files.get(rel))
        self.seq.append(("restore", tuple(changed)))
        self.files = dict(snap)
        return len(changed)

    def repair(self, label, check, text, test_files=None, deadline=None, max_rounds=2, keep_if=None):
        self.seq.append(("repair", label, deadline, max_rounds, text))
        if self.raise_on == label:
            raise RuntimeError("boom")
        before = dict(self.files)
        if self.edits:
            for rel, content in self.edits.pop(0).items():
                if content is None:
                    self.files.pop(rel, None)
                else:
                    self.files[rel] = content
        out = check()
        self.seq.append(("check", out.ok, out.output))
        kept = bool(out.ok) or (keep_if is not None and bool(keep_if()))
        if not kept:
            self.restore(before)
        return kept

    def names(self, inv):
        self.seq.append(("names",))
        missing = [] if SEED in self.files else list(inv["names"])
        return missing, {"completed": True, "missing": {"s": missing} if missing else {}}, []

    def extent(self, end):
        self.seq.append(("extent", end))
        if b"N = 40" in self.files.get(GRID, b""):
            return {"item": "extent", "status": "clean", "result": "far cells rendered"}
        return {"item": "extent", "status": "findings", "result": "far cells never rendered"}

    def e2e(self, project_dir, specs, log=print, **k):
        names = [Path(s).name for s in specs]
        self.seq.append(("e2e", tuple(names)))
        failing = []
        for name in names:
            if b"breaks" in self.files.get(SEED, b""):
                failing.append(name)
            elif b"broken" in self.files.get(GRID, b"") and name in ("A-tabs.spec.js", "B-cells.spec.js"):
                failing.append(name)
            elif b"plainbad" in self.files.get(GRID, b"") and name == "D-plain.spec.js":
                failing.append(name)
        if not failing:
            return self.m.StepResult(True, f"{len(names)} passed")
        return self.m.StepResult(False, "FAILED SPEC FILES: " + ", ".join(failing) + "\n"
                                 + "".join(f"    Error: tab named Sheet1 is not visible ({n})\n" for n in failing))

def drive(tmp: Path, edits, how: str, **world):
    w = World(tmp, edits, **world)
    ref = reference_slot() if how == "ref" else None
    with w.patch, Logs() as lines, settings_with(seed_foundation_staged=(how == "on")):
        if ref is not None:
            ref(w.f)
        else:
            w.f.evolution_seed_foundation_slot()
    return w, list(lines)

def stage_lines(lines):
    return [l for l in lines if l.startswith(("[seed-foundation] stage A kept:", "[seed-foundation] stage A restored:",
                                              "[seed-foundation] stage B kept:", "[seed-foundation] stage B restored:"))]

@case
def the_v163d_switches_and_the_plan():
    from agent import settings

    assert settings.SETTINGS["seed_foundation_staged"] is True
    assert settings.SETTINGS["seed_foundation_staged_sec"] == 2400
    assert settings.SETTINGS["evolution_seed_foundation_sec"] == 1500
    with tempfile.TemporaryDirectory() as tmp:
        w = World(Path(tmp), [])
        inv = w.f.seed_inventory
        assert w.f.seed_foundation_plan(inv)[:2] == (2400.0, True)
        with settings_with(seed_foundation_staged=False):
            assert w.f.seed_foundation_plan(inv)[:2] == (1500.0, False)
        w.f.evolution["health"]["health_check"][0]["status"] = "not run"
        assert w.f.seed_foundation_plan(inv)[:2] == (1500.0, False)
        w.f.evolution["health"]["health_check"][0]["status"] = "findings"
        no_grid = dict(inv, extent={"grid": False})
        assert w.f.seed_foundation_plan(no_grid)[:2] == (1500.0, False)
        w.f.evolution["health"]["seconds"] = 10.0
        assert w.f.evolution_extra_budget([{"id": "A"}]) == 10.0 + 2400
        with settings_with(seed_foundation_staged=False):
            assert w.f.evolution_extra_budget([{"id": "A"}]) == 10.0 + 1500
        w.f.evolution["health"]["health_check"][0]["status"] = "clean"
        assert w.f.evolution_extra_budget([{"id": "A"}]) == 10.0 + 1500

NO_EXTENT_SCENARIOS = {
    "kept": [{SEED: b"seed\n"}],
    "put back by the protected specs": [{SEED: b"breaks\n"}],
    "rolled back by its round": [{"backend/src/database/init_db.js": b"init2\n"}],
}

@case
def without_an_extent_finding_staged_on_is_v163():
    for world in ({"extent_status": "not run"}, {"extent_status": "clean"}, {"extent_status": "findings", "inv_extent": False},
                  {"extent_status": "not run", "protected": False}):
        for label, edits in NO_EXTENT_SCENARIOS.items():
            runs = {}
            for how in ("ref", "off", "on"):
                with tempfile.TemporaryDirectory() as tmp:
                    w, lines = drive(Path(tmp), [dict(e) for e in edits], how, **world)
                    runs[how] = (w.seq, lines, dict(w.files), w.f.evolution["health"]["seed_foundation"], w.reads)
            ref, off, on = runs["ref"], runs["off"], runs["on"]
            where = f"{world} / {label}"
            assert off[:4] == ref[:4], where
            assert on[0] == ref[0], (where, on[0], ref[0])
            assert on[2] == ref[2] and on[3] == ref[3], where
            extra = [l for l in on[1] if l not in ref[1]]
            assert [l for l in on[1] if l not in extra] == ref[1], where
            assert len(extra) == 1 and extra[0].startswith("[seed-foundation] stage A "), (where, extra)
            checks = sum(1 for c in ref[0] if c[0] == "check")
            assert on[4] - ref[4] == checks, (where, on[4], ref[4], checks)
            assert not any(c[0] == "repair" and c[1] == "seed-foundation-grid" for c in on[0]), where

    with tempfile.TemporaryDirectory() as tmp:
        _, lines = drive(Path(tmp), [{SEED: b"seed\n"}], "on", extent_status="not run")
        assert stage_lines(lines) == ["[seed-foundation] stage A kept: backend/src/database/seed_evo.js (new, +1 -0)"], lines
    with tempfile.TemporaryDirectory() as tmp:
        _, lines = drive(Path(tmp), [{SEED: b"breaks\n"}], "on", extent_status="not run")
        assert stage_lines(lines) == ["[seed-foundation] stage A restored: backend/src/database/seed_evo.js (new, +1 -0)"], lines

    with tempfile.TemporaryDirectory() as tmp:
        w0 = World(Path(tmp), [], extent_status="not run")
        w0.files[SEED] = b"seed\n"
        with w0.patch, Logs() as lines:
            w0.f.evolution_seed_foundation_slot()
        assert stage_lines(lines) == [] and w0.f.evolution["health"]["seed_foundation"]["status"] == "clean"

@case
def off_with_an_extent_finding_is_the_v163_one_task():
    for edits in ([{SEED: b"seed\n", GRID: b"const N = 40;\n"}], [{SEED: b"seed\n", GRID: b"const N = 40;\nbroken\n"}],
                  [{SEED: b"seed\n"}, {SEED: b"seed\n", GRID: b"const N = 40;\n"}]):
        runs = {}
        for how in ("ref", "off"):
            with tempfile.TemporaryDirectory() as tmp:
                w, lines = drive(Path(tmp), [dict(e) for e in edits], how)
                runs[how] = (w.seq, lines, dict(w.files), w.f.evolution["health"]["seed_foundation"])
        assert runs["off"] == runs["ref"], edits

    with tempfile.TemporaryDirectory() as tmp:
        w, _ = drive(Path(tmp), [{SEED: b"seed\n", GRID: b"const N = 40;\nbroken\n"}], "off")
        assert w.files == ORIGINAL and w.f.evolution["health"]["seed_foundation"]["status"] == "rolled back"

@case
def a_kept_and_only_b_put_back_when_the_grid_breaks_protected_specs():
    with tempfile.TemporaryDirectory() as tmp:
        w, lines = drive(Path(tmp), [{SEED: b"seed\n"}, {GRID: b"const N = 40;\nplainbad\n"}], "on")
        assert w.files[SEED] == b"seed\n" and w.files[GRID] == ORIGINAL[GRID], w.files
        book = w.f.evolution["health"]["seed_foundation"]
        assert book["status"] == "kept" and book["stage_a"] == "kept" and book["stage_b"] == "restored", book
        assert w.f.evolution["health"]["health_check"][0]["status"] == "findings"
        repairs = [c for c in w.seq if c[0] == "repair"]
        assert [c[1] for c in repairs] == ["seed-foundation", "seed-foundation-grid"]
        a_text, b_text = repairs[0][4], repairs[1][4]
        assert "worksheet grid must render" not in a_text and "start-up extent probe" not in a_text
        assert "GRID WIDENING RULE" in b_text and "up to L40" in b_text and "far cells" not in b_text
        assert "grid renders only up to the tenth column" in b_text and "Pre-provisioned records" not in b_text
        assert repairs[0][2] == NOW + 2400 - 1200 and repairs[1][2] == NOW + 2400 - 1200
        e2e = [c for c in w.seq if c[0] == "e2e"]
        assert e2e == [("e2e", ("A-tabs.spec.js", "B-cells.spec.js", "C-insert.spec.js", "D-plain.spec.js")),
                       ("e2e", ("B-cells.spec.js", "A-tabs.spec.js", "C-insert.spec.js")),
                       ("e2e", ("D-plain.spec.js",))], e2e
        assert w.seq[-1] == ("restore", (GRID,)), w.seq[-1]
        assert stage_lines(lines) == [
            "[seed-foundation] stage A kept: backend/src/database/seed_evo.js (new, +1 -0)",
            "[seed-foundation] stage B restored: frontend/src/components/Grid.tsx (+2 -1) (1 file(s) put back; the fixtures stay)"], lines
        summary = [l for l in lines if l.startswith("[seed-foundation] kept:")]
        assert len(summary) == 1 and summary[0].endswith("; stage A kept, stage B restored"), summary
        assert any("the grid stage broke protected specs ['D-plain.spec.js']" in l for l in lines)

@case
def a_put_back_means_no_stage_b():
    with tempfile.TemporaryDirectory() as tmp:
        w, lines = drive(Path(tmp), [{SEED: b"breaks\n"}, {GRID: b"const N = 40;\n"}], "on")
        assert w.files == ORIGINAL
        book = w.f.evolution["health"]["seed_foundation"]
        assert book["status"] == "rolled back" and book["stage_a"] == "rolled back" and book["stage_b"].startswith("not run"), book
        assert [c[1] for c in w.seq if c[0] == "repair"] == ["seed-foundation"]
        assert any(l.startswith("[seed-foundation] rolled back:") for l in lines)
        assert "[seed-foundation] stage B not run: stage A was not kept (the grid extent stays the batches' inherited debt)" in lines
    with tempfile.TemporaryDirectory() as tmp:

        w, lines = drive(Path(tmp), [{"backend/src/database/init_db.js": b"init2\n"}], "on")
        assert w.files == ORIGINAL and w.f.evolution["health"]["seed_foundation"]["stage_b"].startswith("not run")

@case
def a_red_pre_check_gets_one_more_round():
    with tempfile.TemporaryDirectory() as tmp:
        w, lines = drive(Path(tmp), [{SEED: b"seed\n"}, {GRID: b"const N = 40;\nbroken\n"}, {GRID: b"const N = 40;\n"}], "on")
        repairs = [c for c in w.seq if c[0] == "repair"]
        assert [(c[1], c[3]) for c in repairs] == [("seed-foundation", 2), ("seed-foundation-grid", 2), ("seed-foundation-grid", 0)]
        extra = repairs[2][4]
        assert extra.startswith(repairs[1][4]) and "Your grid edit broke these protected inherited specs" in extra
        assert "Error: tab named Sheet1 is not visible (B-cells.spec.js)" in extra, extra[-600:]
        assert w.files[GRID] == b"const N = 40;\n" and w.files[SEED] == b"seed\n"
        assert w.f.evolution["health"]["seed_foundation"]["stage_b"] == "kept"
        assert ("run_list", tuple(SPECS[1:2] + SPECS[0:1] + SPECS[2:3]), "after the seed foundation's grid stage") in w.seq
        e2e = [c[1] for c in w.seq if c[0] == "e2e"]
        assert e2e[1:] == [("B-cells.spec.js", "A-tabs.spec.js", "C-insert.spec.js"),
                           ("B-cells.spec.js", "A-tabs.spec.js", "C-insert.spec.js"),
                           ("D-plain.spec.js",)], e2e
        assert any(l.startswith("[seed-foundation] stage B pre-check red ['A-tabs.spec.js', 'B-cells.spec.js']") for l in lines)
        assert stage_lines(lines)[-1] == "[seed-foundation] stage B kept: frontend/src/components/Grid.tsx (+1 -1)", lines
    with tempfile.TemporaryDirectory() as tmp:

        w, lines = drive(Path(tmp), [{SEED: b"seed\n"}, {GRID: b"const N = 40;\n"}], "on")
        assert [(c[1], c[3]) for c in w.seq if c[0] == "repair"] == [("seed-foundation", 2), ("seed-foundation-grid", 2)]
        assert any(l.startswith("[seed-foundation] stage B pre-check green") for l in lines)

@case
def b_fails_in_every_other_way_and_a_stays():
    with tempfile.TemporaryDirectory() as tmp:

        w, lines = drive(Path(tmp), [{SEED: b"seed\n"}, {GRID: b"const N = 40;\nbroken\n"}, {GRID: b"const N = 40;\nbroken 2\n"}], "on")
        assert w.files[SEED] == b"seed\n" and w.files[GRID] == ORIGINAL[GRID]
        assert len([c for c in w.seq if c[0] == "repair"]) == 3
        assert w.f.evolution["health"]["seed_foundation"]["stage_b"] == "restored"
        assert w.f.evolution["health"]["seed_foundation"]["status"] == "kept"
    with tempfile.TemporaryDirectory() as tmp:

        w, _ = drive(Path(tmp), [{SEED: b"seed\n"}, {GRID: b"const N = 12;\n"}], "on")
        assert w.files[SEED] == b"seed\n" and w.files[GRID] == ORIGINAL[GRID]
        checks = [c for c in w.seq if c[0] == "check"]
        assert not checks[-1][1] and "grid extent (findings)" in checks[-1][2]
    with tempfile.TemporaryDirectory() as tmp:

        w, lines = drive(Path(tmp), [{SEED: b"seed\n"}], "on", raise_on="seed-foundation-grid")
        assert w.files[SEED] == b"seed\n" and w.files[GRID] == ORIGINAL[GRID]
        assert any("stage B crashed" in l for l in lines) and w.f.evolution["health"]["seed_foundation"]["stage_b"] == "restored"
    with tempfile.TemporaryDirectory() as tmp:

        w = World(Path(tmp), [{SEED: b"seed\n"}, {GRID: b"const N = 40;\n"}])
        w.f.seed_protected_est = lambda: 2200.0
        with w.patch, Logs() as lines, settings_with(seed_foundation_staged=True):
            w.f.evolution_seed_foundation_slot()
        assert w.files[SEED] == b"seed\n" and w.files[GRID] == ORIGINAL[GRID]
        assert w.f.evolution["health"]["seed_foundation"]["stage_b"] == "not run: no time"
        assert any(l.startswith("[seed-foundation] stage B not run: ") for l in lines)
    with tempfile.TemporaryDirectory() as tmp:

        w, _ = drive(Path(tmp), [{SEED: b"seed\n"}, {GRID: b"const N = 40;\n", SEED: None}], "on")
        assert w.files[SEED] == b"seed\n" and w.files[GRID] == ORIGINAL[GRID]
        assert "stated records that were present are now missing" in [c for c in w.seq if c[0] == "check"][-1][2]

@case
def nothing_to_add_runs_b_alone():
    for edits, status in (([{GRID: b"const N = 40;\n"}], "kept"), ([{GRID: b"const N = 40;\nplainbad\n"}], "rolled back")):
        with tempfile.TemporaryDirectory() as tmp:
            w = World(Path(tmp), edits)
            w.files[SEED] = b"seed\n"
            with w.patch, Logs() as lines, settings_with(seed_foundation_staged=True):
                w.f.evolution_seed_foundation_slot()
            book = w.f.evolution["health"]["seed_foundation"]
            assert book["status"] == status and book["stage_a"] == "clean", book
            assert [c[1] for c in w.seq if c[0] == "repair"] == ["seed-foundation-grid"]
            assert any(l.startswith(f"[seed-foundation] {status}: 0 of 0 missing name(s) added") for l in lines), lines
            assert not any(l.startswith("[seed-foundation] stage A") for l in lines)

@case
def the_pre_check_choice_is_deterministic():
    with tempfile.TemporaryDirectory() as tmp:
        w = World(Path(tmp), [])
        assert w.f.seed_grid_precheck_specs() == [SPECS[1], SPECS[0], SPECS[2]]
        assert w.f.seed_grid_precheck_specs(limit=2) == [SPECS[1], SPECS[0]]
        for rel in SPECS:
            (w.f.project_dir / rel).write_text("await expect(page).toHaveTitle('x');\n", encoding="utf-8")
        assert w.f.seed_grid_precheck_specs() == [SPECS[1]]
        (w.f.project_dir / SPECS[3]).write_text("await page.getByRole('tab', { name: 'Sheet2' });\n", encoding="utf-8")
        assert w.f.seed_grid_precheck_specs() == [SPECS[3]]
        w.f.continuation["protected"] = []
        assert w.f.seed_grid_precheck_specs() == [] and w.f.seed_protected_est() == 0.0

@case
def the_stage_b_prompt_is_generic():
    import re

    import main as m

    template = m.Factory.SEED_B_HEAD + m.Factory.SEED_B_RULE + m.Factory.SEED_B_PRE_HEAD
    assert not re.search(r"\bCOLS\b|\bROWS\b|MAX_|\b[A-Z]{1,2}\d{2,}\b|\.tsx|\.jsx|\.js\b|Worksheet[A-Z]", template), template
    assert "dimension constants" in template and "EVERY place that uses them" in template
    assert "max(the minimum range, the used range)" in template and "Never patch individual cells or rows" in template
    assert "at most 5 lines" in template
    assert all(ord(ch) < 128 for ch in template)

class World2(World):

    def __init__(self, tmp, edits, results=(), half_lifecycle=False, restore_incomplete=False, **world):
        super().__init__(tmp, edits, **world)
        self.results = list(results)
        self.untils: list = []
        self.half_lifecycle = half_lifecycle
        self.restore_incomplete = restore_incomplete

    def e2e(self, project_dir, specs, log=print, **k):
        self.untils.append(k.get("until"))
        own = super().e2e(project_dir, specs, log=log)
        script = self.results.pop(0) if self.results else None
        return own if script is None else script(specs)

    def names(self, inv):
        if not self.half_lifecycle:
            return super().names(inv)
        self.seq.append(("names",))

        missing = [inv["names"][0]] if SEED in self.files else list(inv["names"])
        done = b"N = 40" not in self.files.get(GRID, b"")
        return missing, {"completed": done, "missing": {"s": missing}, "notes": [] if done else ["not completed: restart timed out"]}, []

    def restore(self, snap):
        if not self.restore_incomplete:
            return super().restore(snap)
        changed = sorted(rel for rel in set(snap) | set(self.files) if snap.get(rel) != self.files.get(rel))
        self.seq.append(("restore", tuple(changed)))
        return self.m.PutBack(0, complete=False)

def drive2(tmp: Path, edits, **world):
    w = World2(tmp, edits, **world)
    with w.patch, Logs() as lines, settings_with(seed_foundation_staged=True):
        w.f.evolution_seed_foundation_slot()
    return w, list(lines)

def result(ok=False, output="", skipped=False, infra=""):
    import main as m

    return lambda specs: m.StepResult(ok, output, skipped=skipped, infra=infra)

GOOD_B = [{SEED: b"seed\n"}, {GRID: b"const N = 40;\n"}]

@case
def v163e_an_unverified_pre_check_puts_b_back():

    for script, why in ((result(skipped=True), "skipped (Playwright did not run)"),
                        (result(False, "install failed", infra="npm: ETIMEDOUT"), "an infrastructure fault (npm: ETIMEDOUT)"),
                        (result(False, "NOT RUN SPEC FILES: B-cells.spec.js, A-tabs.spec.js, C-insert.spec.js\n"),
                         "not completed: ['A-tabs.spec.js', 'B-cells.spec.js', 'C-insert.spec.js'] not run or run only in part"),
                        (result(False, "FAILED SPEC FILES: \n1 test(s) of C-insert.spec.js not run: the phase's end has passed\n"),
                         "not completed: ['C-insert.spec.js'] not run or run only in part")):
        with tempfile.TemporaryDirectory() as tmp:
            w, lines = drive2(Path(tmp), [dict(e) for e in GOOD_B], results=[None, script])
            assert w.files[SEED] == b"seed\n" and w.files[GRID] == ORIGINAL[GRID], (why, w.files)
            book = w.f.evolution["health"]["seed_foundation"]
            assert book["stage_a"] == "kept" and book["stage_b"] == "restored" and book["status"] == "kept", (why, book)
            assert [c[1] for c in w.seq if c[0] == "repair"] == ["seed-foundation", "seed-foundation-grid"], why
            assert len([c for c in w.seq if c[0] == "e2e"]) == 2, (why, w.seq)
            assert f"[seed-foundation] stage B pre-check did not establish safety ({why}); stage B goes back" in lines, (why, lines)
            line = [l for l in lines if l.startswith("[seed-foundation] stage B restored:")]
            assert len(line) == 1 and line[0].endswith(f" -- unverified: the pre-check ['B-cells.spec.js', 'A-tabs.spec.js', 'C-insert.spec.js']: {why}"), line

@case
def v163e_an_unverified_tail_run_puts_b_back():
    for script in (result(skipped=True), result(False, "NOT RUN SPEC FILES: D-plain.spec.js\n"), result(False, "x", infra="port")):
        with tempfile.TemporaryDirectory() as tmp:
            w, lines = drive2(Path(tmp), [dict(e) for e in GOOD_B], results=[None, None, script])
            assert w.files[SEED] == b"seed\n" and w.files[GRID] == ORIGINAL[GRID], w.files
            assert w.f.evolution["health"]["seed_foundation"]["stage_b"] == "restored"
            assert any(l.startswith("[seed-foundation] stage B protected run did not establish safety (") for l in lines), lines

    with tempfile.TemporaryDirectory() as tmp:
        w = World2(Path(tmp), [dict(e) for e in GOOD_B], results=[None, None, result(False, "FAILED SPEC FILES: D-plain.spec.js\n")])
        w.f.continuation["baseline_red"] = {"D-plain.spec.js": ["t"]}
        with w.patch, Logs() as lines, settings_with(seed_foundation_staged=True):
            w.f.evolution_seed_foundation_slot()
        assert w.f.evolution["health"]["seed_foundation"]["stage_b"] == "kept" and w.files[GRID] == b"const N = 40;\n"

@case
def v163e_only_pre_check_files_that_passed_leave_the_tail_run():

    with tempfile.TemporaryDirectory() as tmp:
        w = World2(Path(tmp), [dict(e) for e in GOOD_B], results=[None, result(False, "FAILED SPEC FILES: A-tabs.spec.js\n")])
        w.f.continuation["baseline_red"] = {"A-tabs.spec.js": ["t"]}
        with w.patch, Logs() as lines, settings_with(seed_foundation_staged=True):
            w.f.evolution_seed_foundation_slot()
        own = [c for c in w.seq if c[0] == "run_list" and c[2] == "after the seed foundation's grid stage"]
        assert own == [("run_list", (SPECS[1], SPECS[2]), "after the seed foundation's grid stage")], own
        assert [c[1] for c in w.seq if c[0] == "e2e"][-1] == ("A-tabs.spec.js", "D-plain.spec.js")
        assert w.f.evolution["health"]["seed_foundation"]["stage_b"] == "kept"

    with tempfile.TemporaryDirectory() as tmp:
        w = World2(Path(tmp), [dict(e) for e in GOOD_B])
        w.f.foundation_run_list = lambda own=(), budget=0, what="": (w.seq.append(("run_list", tuple(own), what)) or [])
        with w.patch, Logs() as lines, settings_with(seed_foundation_staged=True):
            w.f.evolution_seed_foundation_slot()
        assert len([c for c in w.seq if c[0] == "e2e"]) == 2 and w.f.evolution["health"]["seed_foundation"]["stage_b"] == "kept"
        assert "[seed-foundation] stage B protected run: every protected spec it would run passed in the pre-check" in lines

@case
def v163e_stage_b_runs_are_bounded_by_the_slot():

    with tempfile.TemporaryDirectory() as tmp:
        w, _ = drive2(Path(tmp), [dict(e) for e in GOOD_B])
        assert w.untils == [None, NOW + 2400 - 1200, NOW + 2400], w.untils
    with tempfile.TemporaryDirectory() as tmp:

        w, _ = drive2(Path(tmp), [{SEED: b"seed\n"}, {GRID: b"const N = 40;\nbroken\n"}, {GRID: b"const N = 40;\n"}])
        assert w.untils == [None, NOW + 2400 - 1200, NOW + 2400 - 1200, NOW + 2400], w.untils
        assert w.f.evolution["health"]["seed_foundation"]["stage_b"] == "kept"
    with tempfile.TemporaryDirectory() as tmp:

        w, lines = drive2(Path(tmp), [{SEED: b"seed\n"}, {GRID: b"const N = 40;\nbroken\n"}, {GRID: b"const N = 40;\n"}],
                          results=[None, None, result(False, "NOT RUN SPEC FILES: B-cells.spec.js\n")])
        assert w.files[GRID] == ORIGINAL[GRID] and w.f.evolution["health"]["seed_foundation"]["stage_b"] == "restored"
        assert "the protected pre-check could not establish safety: not completed" in [c for c in w.seq if c[0] == "check"][-1][2]

@case
def v163e_a_half_run_lifecycle_check_never_keeps_b():

    with tempfile.TemporaryDirectory() as tmp:
        w, lines = drive2(Path(tmp), [dict(e) for e in GOOD_B], half_lifecycle=True)
        book = w.f.evolution["health"]["seed_foundation"]
        assert book["stage_a"] == "kept" and book["stage_b"] == "restored", book
        assert w.files[SEED] == b"seed\n" and w.files[GRID] == ORIGINAL[GRID]
        last = [c for c in w.seq if c[0] == "check"][-1]
        assert not last[1] and "the data-lifecycle check did not complete (B is unverified)" in last[2], last

@case
def v163e_an_incomplete_put_back_is_never_logged_as_restored():
    with tempfile.TemporaryDirectory() as tmp:
        w, lines = drive2(Path(tmp), [{SEED: b"seed\n"}, {GRID: b"const N = 40;\nplainbad\n"}], restore_incomplete=True)
        assert not any(l.startswith("[seed-foundation] stage B restored:") for l in lines), lines
        assert "[seed-foundation] stage B restore INCOMPLETE: ['frontend/src/components/Grid.tsx']" in lines, lines
        book = w.f.evolution["health"]["seed_foundation"]
        assert book["stage_b"] == "restore incomplete" and book["stage_a"] == "kept", book

    def boom(specs):
        raise RuntimeError("runner died")

    with tempfile.TemporaryDirectory() as tmp:
        w, lines = drive2(Path(tmp), [{SEED: b"seed\n"}], results=[boom])
        assert w.files == ORIGINAL, w.files
        assert w.f.evolution["health"]["seed_foundation"]["status"] == "crashed: RuntimeError"
        assert any(l.startswith("[seed-foundation] rolled back: the slot crashed: RuntimeError('runner died'); 1 file(s) put back") for l in lines), lines
    with tempfile.TemporaryDirectory() as tmp:
        w, lines = drive2(Path(tmp), [{SEED: b"seed\n"}], results=[boom], restore_incomplete=True)
        assert any(l.startswith("[seed-foundation] stage A restore INCOMPLETE: ['backend/src/database/seed_evo.js']") for l in lines), lines
        assert not any("rolled back" in l for l in lines), lines
    with tempfile.TemporaryDirectory() as tmp:

        w = World2(Path(tmp), [])

        def names_crash(inv):
            raise RuntimeError("names")

        w.f.seed_foundation_names = names_crash
        with w.patch, Logs() as lines, settings_with(seed_foundation_staged=True):
            w.f.evolution_seed_foundation_slot()
        assert not any(c[0] == "restore" for c in w.seq) and any("crashed before stage A changed anything" in l for l in lines), lines
    with tempfile.TemporaryDirectory() as tmp:

        w, lines = drive2(Path(tmp), [], raise_on="seed-foundation")
        assert ("restore", ()) in w.seq and any(l.endswith("0 file(s) put back") for l in lines), lines

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            traceback.print_exc()
            print(f"FAIL  {function.__name__}")
    print(f"{len(CASES) - failed}/{len(CASES)} passed")
    return 1 if failed else 0

if __name__ == "__main__":
    os.chdir(ROOT)
    sys.exit(main())
