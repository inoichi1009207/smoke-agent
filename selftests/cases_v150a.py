from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NL = chr(10)
CASES = []

F1 = "backend/test-e2e/REQ-1-1-1.spec.js"
F2 = "backend/test-e2e/REQ-1-1-2.spec.js"
F3 = "backend/test-e2e/REQ-1-1-3.spec.js"
F4 = "backend/test-e2e/REQ-2-1.spec.js"
EARLIER = [F1, F2, F3, F4]
OWN = "backend/test-e2e/REQ-9.spec.js"
APP = "frontend/src/App.tsx"
OWN_TITLE = "REQ-9: works"

def case(function):
    CASES.append(function)
    return function

def title(spec: str) -> str:
    return Path(spec).name.replace(".spec.js", "") + ": Scenario 1"

def brk(spec: str) -> str:
    return f"BREAK<{title(spec)}>"

STAGE2_TREE = {
    "id": "ROOT", "name": "root", "children": [
        {"id": "REQ-3", "name": "Repos", "children": [
            {"id": "REQ-3-1", "name": "Assets", "description": "d"},
            {"id": "REQ-3-2", "name": "Create", "children": [{"id": "REQ-3-2-1", "name": "c"}, {"id": "REQ-3-2-2", "name": "c2"}]}]},
        {"id": "REQ-4", "name": "Issues", "children": [{"id": "REQ-4-1", "name": "list"}, {"id": "REQ-4-2", "name": "new"}]},
    ]}

@case
def spec_ids_split_this_requirement_from_an_earlier_stage():
    from agent import continuation as c

    ids, leaves = c.tree_ids(STAGE2_TREE)
    assert "ROOT" not in ids and {"REQ-3", "REQ-3-2", "REQ-4-1"} <= ids and "REQ-3-2" not in leaves and "REQ-4-1" in leaves
    here = lambda name: c.belongs_here(c.spec_req_id(name), ids, leaves)
    for name in ("REQ-3-1.spec.js", "REQ-3.part2.spec.js", "REQ-4-1-2.spec.js", "DESC-REQ-3-2-1.spec.js", "REQ-3-2.spec.js"):
        assert here(name), name
    for name in ("REQ-1-1-3.spec.js", "REQ-2-2-4.spec.js", "DESC-REQ-1-2.spec.js", "REQ-5.part1.spec.js", "smoke.spec.js", "REQ-31.spec.js"):
        assert not here(name), name
    assert c.spec_req_id("REQ-3.part2.spec.js") == "REQ-3" and c.spec_req_id("other.spec.js") is None

def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

def template_copy(root: Path) -> None:
    import main as m

    m.copy_template(m.AGENT_ROOT / "template", root, overwrite=True)

@case
def detect_needs_other_specs_and_sources_beyond_the_template():
    import main as m
    from agent import continuation as c

    template = m.AGENT_ROOT / "template"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        template_copy(root)
        assert c.detect(root, STAGE2_TREE, template) is None
        write(root, "backend/test-e2e/REQ-3-1.spec.js", "// this requirement's own (an interrupted start)")
        write(root, "backend/src/routes/repos.js", "// written by this requirement's batch")
        assert c.detect(root, STAGE2_TREE, template) is None
        (root / "backend/src/routes/repos.js").unlink()
        write(root, F1, "// stage 1")
        assert c.detect(root, STAGE2_TREE, template) is None
        write(root, "backend/src/routes/auth.js", "// stage 1 auth")
        found = c.detect(root, STAGE2_TREE, template)
        assert found and found["earlier"] == [F1] and found["beyond"] == 1, found
        assert c.detect(root / "missing", STAGE2_TREE, template) is None

class World:

    def __init__(self, root: Path):
        self.root = root
        self.runs: list[tuple[list[str], str]] = []

    def sources(self) -> str:
        base = self.root / "frontend" / "src"
        return NL.join(p.read_text(encoding="utf-8") for p in sorted(base.rglob("*")) if p.is_file())

    def failing(self, spec: str, text: str) -> list[str]:
        if spec == OWN:
            return [] if "OWN-OK" in text else [OWN_TITLE]
        return [title(spec)] if brk(spec) in text else []

    @staticmethod
    def red_output(name: str, titles: list[str]) -> str:
        lines = [f"FAILED SPEC FILES: {name}"]
        for index, t in enumerate(titles, 1):
            lines += [f"  {index}) test-e2e\\{name}:{10 * index}:1 › {t} ───────",
                      "    Error: The requirement says Saved is shown, but nothing visible matches.",
                      "        at fail (D:\\w\\backend\\e2e-support\\outsider.js:43:9)",
                      f"        at D:\\w\\backend\\test-e2e\\{name}:{10 * index + 3}:5"]
        lines += [f"  {len(titles)} failed"] + [f"    test-e2e\\{name}:{10 * i}:1 › {t} ────────" for i, t in enumerate(titles, 1)]
        return NL.join(lines + ["  1 passed"])

    def run_e2e(self, project_dir, specs, log=print):
        from agent.verify import StepResult

        text = self.sources()
        self.runs.append((list(specs), text))
        red = [(spec, self.failing(spec, text)) for spec in specs]
        red = [(spec, titles) for spec, titles in red if titles]
        if len(specs) == 1:
            return StepResult(not red, self.red_output(Path(specs[0]).name, red[0][1]) if red else "1 passed")
        if not red:
            return StepResult(True, f"{len(specs)} of {len(specs)} spec files passed, each run against fresh data")
        head = "FAILED SPEC FILES: " + ", ".join(Path(s).name for s, _ in red)
        parts = [f"## {Path(s).name}{NL}{self.red_output(Path(s).name, t)}" for s, t in red]
        return StepResult(False, NL.join([head, f"{len(specs) - len(red)} of {len(specs)} spec files passed"] + parts))

    def build_frontend(self, project_dir, log=print, deadline=None):
        from agent.verify import StepResult

        return StepResult(True, "")

    def ran(self, spec: str) -> bool:
        return any(spec in specs for specs, _ in self.runs)

def implementer_class(script: list[dict]):
    from agent.codegen import ImplementResult

    class ScriptedImplementer:
        def __init__(self, llm, project_dir, check, **options):
            self.project_dir, self.check = project_dir, check

        def implement(self, node, tests, outcome, extra_context=""):
            step = script.pop(0) if script else {"check": True}
            for rel, content in step.get("write", {}).items():
                path = Path(self.project_dir) / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            written = list(step.get("write", {}))
            if not step.get("check"):
                return ImplementResult(step["status"], written, "scripted", 0)
            result = self.check()
            status = step.get("status") or ("passed" if result.passed else "budget")
            return ImplementResult(status, written, "scripted", 1, last_check=result.output if status == "passed" else "")

        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    return ScriptedImplementer

class _Trace:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None

def make_factory(root: Path, app_before: str, continuation: bool):
    import main as m
    from agent.test_author import AuthoredFile

    (root / "frontend" / "src").mkdir(parents=True, exist_ok=True)
    (root / "backend" / "src").mkdir(parents=True, exist_ok=True)
    (root / APP).write_text(app_before, encoding="utf-8")
    (root / "backend" / "src" / "server.js").write_text("// server", encoding="utf-8")
    f = m.Factory.__new__(m.Factory)
    f.project_dir, f.llm, f.contract, f.native_validation = root, None, "", False
    f.started, f.time_budget, f.spent_before = time.time(), 10800.0, 0.0
    f.trace = _Trace()
    f.report = {"nodes": {}, "verification": {}}
    f.all_test_files, f.e2e_specs, f.green_specs = [], list(EARLIER), list(EARLIER)
    f.file_specs = {}
    f.foundation_checks, f.foundation_baselines, f.regression_debts = {}, {}, []
    f.revised_specs, f.spec_context, f.seed_checks = set(), {}, {}
    f.test_snapshot, f.test_ids_by_file, f.test_titles = {}, {}, {}
    f.large_tree, f.current_trace_id, f.current_members = True, "", []
    f.failed_members, f.no_evidence_members, f.unattempted_members = [], [], []
    f.restore_failures, f.verify_repairs = 0, 0
    f.audit_sent_fingerprints, f.audit_rolled_back_fingerprints = [], []
    f.durations = {"e2e": [(10.0, 1)]}
    f.continuation = {"earlier": list(EARLIER), "protected": list(EARLIER), "baseline_red": {}, "unchecked": []} if continuation else None
    f.record_run = lambda files, ok, output, **kw: None
    f.author_tests = lambda node, prefix, contract: [AuthoredFile(path=OWN, content=f"test('{OWN_TITLE}', async () => {{}});" + NL)]
    return f

def run_batch(app_before: str, script: list[dict], continuation: bool, world_class=None):
    import main as m
    from agent.settings import SETTINGS
    from agent.testrun import TestOutcome

    lines: list[str] = []
    names = ("run_e2e", "build_frontend", "Implementer", "run_node_tests", "static_test_defects", "parse_defects_in_output", "lint_application", "log")
    saved = {name: getattr(m, name) for name in names}
    keys = ("audit_enabled", "regression_repair")
    saved_settings = {key: SETTINGS.get(key, True) for key in keys}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        world = (world_class or World)(root)
        f = make_factory(root, app_before, continuation)
        try:
            m.run_e2e, m.build_frontend = world.run_e2e, world.build_frontend
            m.Implementer = implementer_class(script)
            m.run_node_tests = lambda project_dir, files: TestOutcome(True, "", ran=list(files))
            m.static_test_defects = lambda project_dir, files, **kw: ""
            m.parse_defects_in_output = lambda output, files: ""
            m.lint_application = lambda project_dir, native: ""
            m.log = lines.append
            SETTINGS["audit_enabled"] = False
            SETTINGS["regression_repair"] = False
            passed = f.process_node({"id": "REQ-9", "name": "Batch nine", "members": ["REQ-9"]}, 1)
            after = (root / APP).read_text(encoding="utf-8") if (root / APP).is_file() else None
        finally:
            for name, value in saved.items():
                setattr(m, name, value)
            SETTINGS.update(saved_settings)
    return f, world, lines, after, passed

def foundation_put_back(lines: list[str]) -> bool:
    return any("FOUNDATION" in line and "put back to the state before this batch" in line for line in lines)

def red_end_put_back(lines: list[str]) -> bool:
    return any("AND the foundation specs fail now" in line and "put back" in line for line in lines)

@case
def a_fresh_start_keeps_the_first_three_as_the_foundation():
    import main as m

    f = m.Factory.__new__(m.Factory)
    f.green_specs = [F1, F2, F3, F4]
    for state in (None, {"earlier": [F4], "protected": [], "baseline_red": {}}):
        f.continuation = state
        assert f.protected_specs() == [] and f.foundation_specs() == [F1, F2, F3]
        assert f.foundation_run_list([F2]) == [F1, F3] and f.foundation_run_list() == [F1, F2, F3]
    g = m.Factory.__new__(m.Factory)
    g.green_specs = [F1, F2]
    assert g.foundation_specs() == [F1, F2] and g.protected_specs() == []

@case
def a_green_batch_that_breaks_a_protected_spec_is_put_back_as_a_foundation_break():
    before = "base"
    after = "base OWN-OK " + brk(F4)
    f, world, lines, app, passed = run_batch(before, [{"write": {APP: after}, "check": True}], continuation=True)
    assert app == before and foundation_put_back(lines) and not passed, lines[-12:]
    assert f.green_specs[:4] == EARLIER, f.green_specs

    f, world, lines, app, passed = run_batch(before, [{"write": {APP: after}, "check": True}], continuation=False)
    assert not foundation_put_back(lines), lines[-12:]
    assert "REQ-9" in f.regression_debts and F4 not in f.green_specs, (f.regression_debts, f.green_specs)

@case
def a_red_batch_that_breaks_a_protected_spec_is_put_back_at_its_end():
    before = "base"
    after = "base " + brk(F4)
    f, world, lines, app, passed = run_batch(before, [{"write": {APP: after}, "check": True}], continuation=True)
    assert app == before and not passed, lines[-12:]
    assert red_end_put_back(lines) or foundation_put_back(lines), lines[-12:]
    f, world, lines, app, passed = run_batch(before, [{"write": {APP: after}, "check": True}], continuation=False)
    assert app == after and not red_end_put_back(lines), lines[-12:]
    assert not world.ran(F4)

@case
def a_protected_spec_red_before_the_batch_is_not_blamed_on_it():
    before = "base " + brk(F4)
    after = before + " OWN-OK"
    f, world, lines, app, passed = run_batch(before, [{"write": {APP: after}, "check": True}], continuation=True)
    assert app == after and not foundation_put_back(lines), lines[-12:]
    assert any("pre-existing foundation failure" in line and "REQ-2-1.spec.js" in line for line in lines), lines[-12:]

    assert [F4] in [specs for specs, seen in world.runs if seen == before], world.runs

class FlakyWorld(World):

    def run_e2e(self, project_dir, specs, log=print):
        from agent.verify import StepResult

        text = self.sources()
        if "FLAKY" in text and F4 in specs and len(specs) > 1:
            self.runs.append((list(specs), text))
            head = "FAILED SPEC FILES: " + Path(F4).name
            return StepResult(False, NL.join([head, f"## {Path(F4).name}", self.red_output(Path(F4).name, [title(F4)])]))
        return World.run_e2e(self, project_dir, specs, log=log)

@case
def a_protected_spec_that_passes_alone_does_not_put_a_batch_back():
    f, world, lines, app, passed = run_batch("base", [{"write": {APP: "base OWN-OK FLAKY"}, "check": True}], continuation=True,
                                             world_class=FlakyWorld)
    assert app == "base OWN-OK FLAKY" and not foundation_put_back(lines), lines[-12:]
    assert any("pass alone on this state" in line for line in lines), lines[-12:]
    assert F4 in f.green_specs, f.green_specs
    assert [F4] in [specs for specs, _ in world.runs], world.runs

@case
def the_red_runs_take_a_rotating_window_of_the_protected_specs():
    import main as m

    f = m.Factory.__new__(m.Factory)
    extra = [f"backend/test-e2e/REQ-2-{i}.spec.js" for i in range(1, 9)]
    f.green_specs = [F1, F2, F3] + extra + ["backend/test-e2e/REQ-3-1.spec.js"]
    f.continuation = {"protected": [F1, F2, F3] + extra}
    lines: list[str] = []
    saved = m.log
    m.log = lines.append
    try:
        f.durations = {"e2e": [(10.0, 1)]}
        assert f.foundation_run_list((), 1200.0) == [F1, F2, F3] + extra and not lines
        f.durations = {"e2e": [(100.0, 1)]}
        seen: list[str] = []
        for _ in range(4):
            run = f.foundation_run_list((), 900.0, "test")
            assert run[:3] == [F1, F2, F3] and len(run) == 5, run
            seen += run[3:]
        assert set(seen) == set(extra) and "REQ-3-1.spec.js" not in " ".join(seen), seen
        assert lines and "rotating" in lines[0], lines
        assert f.foundation_specs() == [F1, F2, F3] + extra
    finally:
        m.log = saved

class StopDry(Exception):
    pass

class NoModel:
    model = "no-model (self-test)"

    def chat(self, *args, **kwargs):
        raise AssertionError("a model call in the dry start")

    def summary(self):
        return {}

STAGE2_YAML = """id: ROOT
name: Stage two
type: FOLDER
description: The repository part of the product.
children:
- id: REQ-3
  name: Repositories
  type: FOLDER
  description: Repositories.
  children:
  - id: REQ-3-1
    name: Repository assets
    type: ATOMIC
    description: The repository page lists the files.
    scenarios:
    - steps:
      - keyword: GIVEN
        content: the user is signed in
      - keyword: THEN
        content: the file list is shown
  - id: REQ-3-2
    name: New repository
    type: ATOMIC
    description: The `New repository` button creates a repository.
"""

def stage1_app(root: Path) -> None:
    template_copy(root)
    write(root, APP, "export default function App() { return <Routes><Route path=\"/login\" element={<LoginPage />} /></Routes>; } // STAGE-1")
    write(root, "backend/src/routes/auth.js", "router.post('/login', async (req, res) => {});\n")
    write(root, "backend/src/database/init_db.js", "CREATE TABLE IF NOT EXISTS users (\n id INTEGER PRIMARY KEY,\n username TEXT NOT NULL\n);\n// STAGE-1 SEED 'alice-dev'\n")
    write(root, F1, 'test("REQ-1-1-1: x", async ({ page }) => { await page.goto("/"); await click(page, { role: "button", name: "Sign in" }); });\n')
    write(root, "backend/test-e2e/REQ-1-2.spec.js", 'test("REQ-1-2: y", async () => {});\n')

def dry_start(project: Path, requirements: Path, red: set[str], marker: dict) -> tuple[list[str], dict]:
    import main as m
    from agent import verify, watchdog
    from agent.verify import StepResult

    lines: list[str] = []
    seen: dict = {"e2e": [], "contract_kwargs": None, "copy_overwrite": None, "queue": None}

    def fake_e2e(project_dir, specs, log=print, until=None, **kw):
        seen["e2e"].append(list(specs))
        bad = [s for s in specs if Path(s).name in red]
        if not bad:
            return StepResult(True, f"{len(specs)} of {len(specs)} spec files passed")
        return StepResult(False, "FAILED SPEC FILES: " + ", ".join(Path(s).name for s in bad) + NL + NL.join(
            World.red_output(Path(s).name, [Path(s).name.replace(".spec.js", "") + ": y"]) for s in bad))

    def fake_contract(llm, tree, project_dir, log=print, **kwargs):
        seen["contract_kwargs"] = dict(kwargs)
        return json.dumps({"pages": [{"route": "/", "file": "frontend/src/pages/HomePage.tsx"}], "notes": []})

    real_copy = m.copy_template

    def spy_copy(template_dir, output_dir, overwrite=True):
        seen["copy_overwrite"] = overwrite
        return real_copy(template_dir, output_dir, overwrite=overwrite)

    def fake_queue(self, nodes, done_ids, digest, description):
        seen["queue"] = {"done": list(done_ids), "green": list(self.green_specs), "e2e": list(self.e2e_specs),
                         "large": self.large_tree, "contract": self.contract, "continuation": self.continuation,
                         "seeds": [str(n.get("seed_sentences") or "") for n, _ in nodes],
                         "debt_notes": {str(n.get("id")): str(n.get("handoff_debt_note") or "") for n, _ in nodes}}
        if marker.get("book"):
            done_ids.append(str(nodes[0][0].get("id")))
            self.save_checkpoint(digest, done_ids, description)
        raise StopDry()

    names = ("log", "build_contract", "attach_visual_notes", "run_e2e", "build_frontend", "Trace", "llm_from_env", "copy_template")
    saved = {name: getattr(m, name) for name in names}
    saved_install, saved_queue = verify.install_with_retries, m.Factory.run_queue
    try:
        m.log = lines.append
        m.build_contract = fake_contract
        m.attach_visual_notes = lambda tree, requirements_dir, project_dir, llm, log=print: 0
        m.run_e2e = fake_e2e
        m.build_frontend = lambda project_dir, log=print, deadline=None: StepResult(True, "")
        m.Trace = lambda project_dir, log=print: _Trace()
        m.llm_from_env = lambda usage, log=print: NoModel()
        m.copy_template = spy_copy
        verify.install_with_retries = lambda project_dir, package, log=print: StepResult(True, "")
        m.Factory.run_queue = fake_queue
        try:
            m.Factory(requirements, project).run()
        except StopDry:
            pass
    finally:
        for name, value in saved.items():
            setattr(m, name, value)
        verify.install_with_retries, m.Factory.run_queue = saved_install, saved_queue
        try:
            watchdog.shutdown(log=lambda line: None)
        except Exception:
            pass
    return lines, seen

def requirement_dir(root: Path, text: str = STAGE2_YAML) -> Path:
    folder = root / "req"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "requirements.yaml").write_text(text, encoding="utf-8")
    return folder

@case
def a_fresh_start_runs_as_v149():
    import main as m

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project = root / "out"
        template_copy(project)
        write(project, APP, "// a leftover edit of the starter: a fresh start puts the template back, as v149")
        lines, seen = dry_start(project, requirement_dir(root), set(), {})
        assert seen["queue"] is not None, lines[-10:]
        assert seen["copy_overwrite"] is True
        assert (project / APP).read_bytes() == (m.AGENT_ROOT / "template" / APP).read_bytes()
        assert not any("[continuation]" in line for line in lines), [l for l in lines if "continuation" in l]
        assert seen["contract_kwargs"] == {}, seen["contract_kwargs"]
        assert seen["e2e"] == [] and seen["queue"]["green"] == [] and seen["queue"]["continuation"] is None
        assert seen["queue"]["large"] is False
        assert not any("EXISTING APPLICATION" in s for s in seen["queue"]["seeds"])

@case
def a_continuation_keeps_the_earlier_app_and_protects_its_green_specs():
    import main as m

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project = root / "out"
        stage1_app(project)
        stage1_app_text = (project / APP).read_text(encoding="utf-8")
        requirements = requirement_dir(root)

        digest = m.requirement_digest(m.load_tree(requirements))
        write(project, ".arc/resume.json", json.dumps({"digest": digest, "done": ["REQ-3-1"], "sources": {}}))
        lines, seen = dry_start(project, requirements, {"REQ-1-2.spec.js"}, {})
        queue = seen["queue"]
        assert queue is not None, lines[-10:]
        assert queue["done"] == [] and any("without a requirement identity" in line for line in lines), lines[:20]
        assert seen["copy_overwrite"] is False and (project / APP).read_text(encoding="utf-8") == stage1_app_text
        assert (project / "backend/src/routes/auth.js").is_file() and "STAGE-1 SEED" in (project / "backend/src/database/init_db.js").read_text(encoding="utf-8")

        assert seen["e2e"] == [[F1, "backend/test-e2e/REQ-1-2.spec.js"], ["backend/test-e2e/REQ-1-2.spec.js"]], seen["e2e"]
        cont = queue["continuation"]
        assert cont["protected"] == [F1] and list(cont["baseline_red"]) == ["REQ-1-2.spec.js"] and cont["unchecked"] == [], cont
        assert queue["green"] == [F1] and queue["e2e"][0] == F1 and "backend/test-e2e/REQ-1-2.spec.js" not in queue["e2e"]

        assert "EXISTING APPLICATION" in (seen["contract_kwargs"] or {}).get("existing", ""), seen["contract_kwargs"]
        notes = json.loads(queue["contract"]).get("notes") or []
        assert any(str(n).startswith("EXISTING APPLICATION") and "/login" in str(n) and "users(id, username)" in str(n) for n in notes), notes
        assert all("EXISTING APPLICATION (earlier stage)" in s and '"Sign in"' in s for s in queue["seeds"]), queue["seeds"][:1]
        assert not any("`" in s.split("EXISTING APPLICATION (earlier stage)")[1] for s in queue["seeds"])

        assert queue["large"] is True and any("large-tree guards" in line for line in lines)

        assert (project / F1).read_text(encoding="utf-8").startswith('test("REQ-1-1-1')

@case
def a_resume_of_the_same_stage_keeps_its_baseline_and_another_requirement_does_not_resume():
    import main as m

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project = root / "out"
        stage1_app(project)
        requirements = requirement_dir(root)
        lines, seen = dry_start(project, requirements, {"REQ-1-2.spec.js"}, {"book": True})
        state = json.loads((project / ".arc" / "resume.json").read_text(encoding="utf-8"))
        assert state["req_sha"] and state["continuation"]["protected"] == [F1] and len(state["done"]) == 1, state.get("done")
        write(project, APP, (project / APP).read_text(encoding="utf-8") + " // stage-2 work")

        lines, seen = dry_start(project, requirements, set(), {})
        queue = seen["queue"]
        assert queue["done"] == state["done"] and seen["e2e"] == [], (queue["done"], seen["e2e"])

        assert seen["copy_overwrite"] is False and (project / APP).read_text(encoding="utf-8").endswith("// STAGE-1")
        assert queue["continuation"]["protected"] == [F1] and queue["green"][0] == F1
        assert any("[continuation] resumed" in line for line in lines), lines[:20]

        requirements2 = requirement_dir(root / "next", STAGE2_YAML + "# stage 3\n")
        assert m.requirement_digest(m.load_tree(requirements2)) == m.requirement_digest(m.load_tree(requirements))
        lines, seen = dry_start(project, requirements2, set(), {})
        assert seen["queue"]["done"] == [] and any("another requirements.yaml" in line for line in lines), lines[:20]

@case
def the_description_pass_never_removes_an_earlier_stages_desc_specs():
    from types import SimpleNamespace

    from agent import description_pass as dp

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        old = "backend/test-e2e/DESC-REQ-1-2.spec.js"
        mine = "backend/test-e2e/DESC-REQ-3-1.spec.js"
        write(root, old, "// stage 1, admitted then")
        write(root, mine, "// this pass's candidate, not admitted")
        for continuation, kept in (({"earlier": [old]}, {old}), (None, set())):
            write(root, old, "// stage 1, admitted then")
            write(root, mine, "// candidate")
            fake = SimpleNamespace(project_dir=root, description_pass={}, description_specs=[], continuation=continuation)
            run = dp.DescriptionPass.__new__(dp.DescriptionPass)
            run.f, run.dir, run.state, run.log = fake, root, {}, lambda line: None
            run.remove_leftovers()
            left = {p.relative_to(root).as_posix() for p in (root / "backend" / "test-e2e").glob("*.spec.js")}
            assert left == kept, (continuation, left)

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=6)
    print(f"{len(CASES) - failed}/{len(CASES)} self-tests passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
