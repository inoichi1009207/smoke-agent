from __future__ import annotations

import json
import sys
import tempfile
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml

import cases_v150a as v

NL = chr(10)
CASES = []

SPEC_A = "backend/test-e2e/REQ-1-1-1.spec.js"
SPEC_B = "backend/test-e2e/REQ-2.part1.spec.js"
APP = "frontend/src/App.tsx"
APP_JS = "backend/src/app.js"
INIT_DB = "backend/src/database/init_db.js"
ROUTES = "backend/src/routes/repos.js"
INFRA = "backend/src/database/prepare_e2e.js"
STARTER_PAGE = "frontend/src/pages/HomePage.tsx"

def case(function):
    CASES.append(function)
    return function

def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

@contextmanager
def patched(**names):
    import main as m

    saved = {name: getattr(m, name) for name in names}
    try:
        for name, value in names.items():
            setattr(m, name, value)
        yield
    finally:
        for name, value in saved.items():
            setattr(m, name, value)

def steps(n: int) -> list[dict]:
    return [{"keyword": "GIVEN" if i == 0 else "THEN", "content": f"step {i + 1} holds"} for i in range(n)]

def leaf(leaf_id: str, name: str, description: str, n_steps: int = 2) -> dict:
    return {"id": leaf_id, "name": name, "type": "ATOMIC", "description": description,
            "scenarios": [{"name": f"{leaf_id}: main flow", "steps": steps(n_steps)}]}

def folder(folder_id: str, name: str, *children) -> dict:
    return {"id": folder_id, "name": name, "type": "FOLDER", "description": f"{name}.", "children": list(children)}

SIGN_IN = leaf("REQ-1-1-1", "Sign in", "The `Sign in` button signs the user in with the seeded account.")
ORIGINAL_STAR = "The `Star` button stars a repository. The star count is shown next to the button."
OLD_TREE = {"id": "ROOT", "name": "Code hosting", "type": "FOLDER", "description": "The product.", "children": [
    folder("REQ-1", "Identity", folder("REQ-1-1", "Access", SIGN_IN)),
    folder("REQ-2", "Repositories",
           leaf("REQ-2-1", "Repository list", "The repositories page lists every repository of the user."),
           leaf("REQ-2-2", "Star a repository", ORIGINAL_STAR)),
]}
MODIFIED_STAR = ("Original Feature Description" + NL + NL + ORIGINAL_STAR + NL + NL + "Modified Feature Description" + NL + NL
                 + "The `Star` button now toggles: a second press removes the star. The star count is shown next to the button.")

NEW_TREE = {"id": "ROOT", "name": "Code hosting", "type": "FOLDER", "description": "The product.", "children": [
    folder("REQ-1", "Identity", folder("REQ-1-1", "Access", SIGN_IN)),
    folder("REQ-2", "Repositories", leaf("REQ-2-2", "Star a repository", MODIFIED_STAR, 7)),
    folder("REQ-3", "Issues", leaf("REQ-3-1", "Open an issue", "The `New issue` button opens an issue form.", 7)),
]}

def dump(tree: dict) -> str:
    return yaml.safe_dump(tree, sort_keys=False, allow_unicode=True, width=1000)

OLD_YAML = dump(OLD_TREE)
NEW_YAML = dump(NEW_TREE)

SPEC_A_TEXT = ("const { test } = require('@playwright/test');" + NL
               + "test('REQ-1-1-1: signs in with the seeded account', async ({ page }) => {" + NL
               + "  await page.goto('/login');" + NL
               + "  await click(page, { role: 'button', name: 'Sign in' });" + NL + "});" + NL)
SPEC_B_TEXT = ("const { test } = require('@playwright/test');" + NL
               + "test('REQ-2-1: lists the repositories', async ({ page }) => { await page.goto('/repos'); });" + NL
               + "test('REQ-2-2: stars a repository', async ({ page }) => { await click(page, { role: 'button', name: 'Star' }); });" + NL
               + "test('REQ-2-2: shows the star count', async ({ page }) => { await page.goto('/repos/1'); });" + NL)
B_TITLES = ["REQ-2-1: lists the repositories", "REQ-2-2: stars a repository", "REQ-2-2: shows the star count"]

def earlier_round_factory(root: Path) -> SimpleNamespace:
    contract = {"pages": [{"route": "/login", "file": "frontend/src/pages/LoginPage.tsx"}],
                "api": [{"method": "GET", "path": "/api/repos"}], "notes": ["Stars live in the stars table"]}
    return SimpleNamespace(
        contract=json.dumps(contract),
        report={"nodes": {"REQ-1-1-1": {"status": "passed", "tests": [SPEC_A]},
                          "REQ-2.part1": {"status": "passed", "tests": [SPEC_B]}}},
        batch_members={"REQ-1-1-1": ["REQ-1-1-1"], "REQ-2.part1": ["REQ-2-1", "REQ-2-2"]},
        failed_members=[], unattempted_members=[], description_specs=[], open_findings={}, continuation=None,
        project_dir=root)

def inherited_app(project: Path) -> None:
    from agent import handoff as h
    import hashlib

    v.template_copy(project)
    write(project, APP, 'export default function App() { return <Routes><Route path="/repos" element={<ReposPage />} /></Routes>; } // ROUND-1')
    write(project, APP_JS, "const express = require('express');\nconst app = express();\napp.use('/api/repos', require('./routes/repos'));\n// ROUND-1\n")
    write(project, INIT_DB, "CREATE TABLE IF NOT EXISTS repos (\n id INTEGER PRIMARY KEY,\n name TEXT NOT NULL,\n stars INTEGER DEFAULT 0\n);\n// ROUND-1 SEED 'octo-repo'\n")
    write(project, ROUTES, "router.get('/', async (req, res) => {});\nrouter.post('/:id/star', async (req, res) => {});\n")
    write(project, SPEC_A, SPEC_A_TEXT)
    write(project, SPEC_B, SPEC_B_TEXT)
    write(project, "requirements/requirements.yaml", OLD_YAML)
    (project / INFRA).unlink()
    (project / STARTER_PAGE).unlink()
    old_sha = hashlib.sha256(OLD_YAML.encode("utf-8")).hexdigest()
    assert h.write_handoff(earlier_round_factory(project), OLD_TREE, old_sha, OLD_YAML, project, log=lambda line: None)

def app_files(project: Path) -> dict[str, bytes]:
    out = {}
    for top in ("frontend/src", "backend/src", "backend/test-e2e"):
        base = project / top
        for path in sorted(base.rglob("*")) if base.is_dir() else []:
            if path.is_file():
                out[path.relative_to(project).as_posix()] = path.read_bytes()
    return out

def requirement_dir(root: Path, text: str) -> Path:
    folder_path = root / "req"
    folder_path.mkdir(parents=True, exist_ok=True)
    (folder_path / "requirements.yaml").write_text(text, encoding="utf-8")
    return folder_path

def dry_start(project: Path, requirements: Path, red: set[str] = frozenset(), book: bool = False) -> tuple[list[str], dict]:
    import main as m
    from agent import verify, watchdog
    from agent.verify import StepResult

    lines: list[str] = []
    seen: dict = {"e2e": [], "contract_kwargs": None, "copies": [], "queue": None, "factory": None, "checkpoint": None}

    def fake_e2e(project_dir, specs, log=print, until=None, **kw):
        seen["e2e"].append(list(specs))
        bad = [s for s in specs if Path(s).name in red]
        if not bad:
            return StepResult(True, f"{len(specs)} of {len(specs)} spec files passed")
        return StepResult(False, "FAILED SPEC FILES: " + ", ".join(Path(s).name for s in bad) + NL + NL.join(
            v.World.red_output(Path(s).name, [Path(s).name.replace(".spec.js", "") + ": y"]) for s in bad))

    def fake_contract(llm, tree, project_dir, log=print, **kwargs):
        seen["contract_kwargs"] = dict(kwargs)
        return json.dumps({"pages": [{"route": "/", "file": "frontend/src/pages/HomePage.tsx"}], "notes": []})

    real_copy = m.copy_template

    def spy_copy(template_dir, output_dir, overwrite=True, only=None):
        seen["copies"].append({"overwrite": overwrite, "only": only})
        return real_copy(template_dir, output_dir, overwrite=overwrite, only=only)

    def fake_queue(self, nodes, done_ids, digest, description):
        seen["factory"] = self
        seen["queue"] = {"done": list(done_ids), "nodes": [n for n, _ in nodes], "green": list(self.green_specs),
                         "e2e": list(self.e2e_specs), "large": self.large_tree, "contract": self.contract,
                         "continuation": self.continuation}
        if book:
            self.save_checkpoint(digest, list(done_ids), description)
            path = self.project_dir / ".arc" / "resume.json"
            seen["checkpoint"] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        raise v.StopDry()

    names = ("log", "build_contract", "attach_visual_notes", "run_e2e", "build_frontend", "Trace", "llm_from_env", "copy_template")
    saved = {name: getattr(m, name) for name in names}
    saved_install, saved_queue = verify.install_with_retries, m.Factory.run_queue
    try:
        m.log = lines.append
        m.build_contract = fake_contract
        m.attach_visual_notes = lambda tree, requirements_dir, project_dir, llm, log=print: 0
        m.run_e2e = fake_e2e
        m.build_frontend = lambda project_dir, log=print, deadline=None: StepResult(True, "")
        m.Trace = lambda project_dir, log=print: v._Trace()
        m.llm_from_env = lambda usage, log=print: v.NoModel()
        m.copy_template = spy_copy
        verify.install_with_retries = lambda project_dir, package, log=print: StepResult(True, "")
        m.Factory.run_queue = fake_queue
        try:
            m.Factory(requirements, project).run()
        except v.StopDry:
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

_EVOLUTION_START: dict = {}

def evolution_start() -> dict:
    if not _EVOLUTION_START:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "out"
            inherited_app(project)
            before = app_files(project)
            lines, seen = dry_start(project, requirement_dir(root, NEW_YAML), set(), book=True)
            _EVOLUTION_START.update(lines=lines, seen=seen, before=before, after=app_files(project),
                                    infra_back=(project / INFRA).is_file(), page_back=(project / STARTER_PAGE).is_file())
    return _EVOLUTION_START

def batch_of(nodes: list[dict], member: str) -> dict:
    found = [n for n in nodes if member in (n.get("members") or [n.get("id")])]
    assert len(found) == 1, [(n.get("id"), n.get("members")) for n in nodes]
    return found[0]

@case
def an_evolution_round_keeps_the_inherited_app_and_builds_only_modified_and_new_leaves():
    import main as m

    run = evolution_start()
    lines, seen = run["lines"], run["seen"]
    queue = seen["queue"]
    assert queue is not None, lines[-12:]
    f = seen["factory"]

    changed = [rel for rel, data in run["before"].items() if run["after"].get(rel) != data]
    assert not changed, changed
    assert seen["copies"] == [{"overwrite": False, "only": m.infra_template_file}], seen["copies"]
    assert run["infra_back"] and not run["page_back"], (run["infra_back"], run["page_back"])
    assert any("never overwritten by the starter template" in line for line in lines)

    members = sorted(tuple(n.get("members") or [n.get("id")]) for n in queue["nodes"])
    assert members == [("REQ-2-2",), ("REQ-3-1",)], members

    cont = queue["continuation"]
    assert sorted(cont["earlier"]) == sorted([SPEC_A, SPEC_B]) and cont.get("evolution") is True, cont.get("earlier")
    assert cont["baseline"] == "taken" and sorted(cont["protected"]) == sorted([SPEC_A, SPEC_B]), cont

    assert str(cont.get("contract_note") or "").startswith("EXISTING APPLICATION (inherited from the original round"), \
        str(cont.get("contract_note"))[:200]
    notes = [str(n) for n in json.loads(queue["contract"]).get("notes") or []]
    assert any(n.startswith("EXISTING APPLICATION (inherited from the original round") for n in notes), [n[:80] for n in notes]
    assert any("DATA LIFECYCLE" in n for n in notes), [n[:80] for n in notes]

    assert batch_of(queue["nodes"], "REQ-2-2").get("spec_prefix") == "REQ-2-2.evo", batch_of(queue["nodes"], "REQ-2-2").get("spec_prefix")
    assert f.spec_prefix_of(batch_of(queue["nodes"], "REQ-2-2")) == "REQ-2-2.evo"
    assert f.spec_prefix_of(batch_of(queue["nodes"], "REQ-3-1")) == "REQ-3-1"

    state = f.evolution_state()
    classes = state["classes"]
    assert (len(classes["inherited"]), len(classes["modified"]), len(classes["new"])) == (1, 1, 1), classes
    assert classes["inherited"] == ["REQ-1-1-1"] and classes["modified"] == ["REQ-2-2"] and classes["new"] == ["REQ-3-1"]
    assert classes["out_of_tree"] == ["REQ-2-1"] and classes["inventory_complete"] is True, classes

    tests = {t["title"]: t["class"] for t in state["registry"]["files"][SPEC_B]["tests"]}
    assert tests == {B_TITLES[0]: "valid", B_TITLES[1]: "unverified", B_TITLES[2]: "unverified"}, tests
    assert state["tests"][SPEC_B] == {t: "passed" for t in B_TITLES}, state.get("tests")

    assert sorted(f._frozen_inherited_specs) == sorted([SPEC_A, SPEC_B])
    checkpoint = seen["checkpoint"] or {}
    assert checkpoint.get("evolution", {}).get("classes", {}).get("modified") == ["REQ-2-2"], list(checkpoint)
    assert sorted(checkpoint.get("evolution_frozen") or {}) == sorted([SPEC_A, SPEC_B])

    assert queue["large"] is True

@case
def the_batch_of_a_modified_leaf_carries_its_effective_rules():
    run = evolution_start()
    queue = run["seen"]["queue"]
    assert queue is not None, run["lines"][-12:]
    batch = batch_of(queue["nodes"], "REQ-2-2")

    assert "CURRENTLY EFFECTIVE RULES of REQ-2-2" in str(batch.get("ancestor_context") or ""), str(batch.get("ancestor_context"))[:300]

    assert "CURRENTLY EFFECTIVE RULES of REQ-2-2" in str(batch.get("effective_rules") or ""), sorted(batch)
    assert batch.get("evolution", {}).get("members") == {"REQ-2-2": "modified"}, batch.get("evolution")

@case
def the_earlier_rounds_handoff_reaches_the_architect_in_an_evolution_round():
    run = evolution_start()
    seen = run["seen"]
    assert seen["queue"] is not None, run["lines"][-12:]
    existing = str((seen["contract_kwargs"] or {}).get("existing") or "")
    assert existing.startswith("EXISTING APPLICATION (inherited from the original round"), existing[:200]

    assert "HANDOFF OF THE EARLIER STAGE" in existing, existing[-400:]
    assert (seen["queue"]["continuation"] or {}).get("handoff"), sorted(seen["queue"]["continuation"] or {})

@case
def a_fresh_start_has_no_evolution_round():
    import main as m

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project = root / "out"
        v.template_copy(project)
        lines, seen = dry_start(project, requirement_dir(root, NEW_YAML))
        assert seen["queue"] is not None, lines[-12:]
        assert seen["copies"] == [{"overwrite": True, "only": None}], seen["copies"]
        f = seen["factory"]
        assert f.evolution_state() is None and "evolution" not in f.__dict__, f.__dict__.get("evolution")
        assert seen["queue"]["continuation"] is None and seen["e2e"] == [] and seen["contract_kwargs"] == {}
        assert not any("[evolution]" in line for line in lines), [line for line in lines if "[evolution]" in line]
        assert not (project / m.Factory.INHERITED_BACKEND_DIR).exists()
        assert all(not n.get("spec_prefix") for n in seen["queue"]["nodes"])

@case
def a_later_stage_keeps_the_v150_continuation_path():
    import cases_v151a as w
    from agent import handoff as h

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project = root / "out"
        v.stage1_app(project)
        h.write_handoff(w.stage1_factory(project), w.STAGE1_TREE, "stage-1-sha", w.STAGE1_REQUIREMENT, project, log=lambda line: None)
        stage1_text = (project / v.APP).read_bytes()
        lines, seen = dry_start(project, requirement_dir(root, w.STAGE2_YAML))
        assert seen["queue"] is not None, lines[-12:]
        f = seen["factory"]
        assert f.inherited_app.get("preserve") is True
        assert f.evolution_state() is None
        assert any("no evolution round" in line for line in lines), [line for line in lines if "[evolution]" in line]
        assert seen["copies"] == [{"overwrite": False, "only": None}], seen["copies"]
        cont = seen["queue"]["continuation"]
        assert cont is not None and "evolution" not in cont, cont
        assert cont["earlier"] == [v.F1, "backend/test-e2e/REQ-1-2.spec.js"], cont["earlier"]
        assert (project / v.APP).read_bytes() == stage1_text
        assert "HANDOFF OF THE EARLIER STAGE" in (seen["contract_kwargs"] or {}).get("existing", "")
        assert all(not n.get("spec_prefix") for n in seen["queue"]["nodes"])

@case
def preservation_is_fail_safe_when_the_scan_raises():
    import main as m
    from agent import continuation as c
    from agent import evolution

    def boom(project_dir, template_dir):
        raise RuntimeError("scan failed")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write(root, APP, "// an application")
        saved = c.sources_beyond_template
        try:
            c.sources_beyond_template = boom
            got = evolution.preservation(root, m.AGENT_ROOT / "template")
            empty = evolution.preservation(root / "empty", m.AGENT_ROOT / "template")
        finally:
            c.sources_beyond_template = saved
        assert got["preserve"] is True and "fail-safe" in got["reason"], got
        assert empty["preserve"] is False, empty

CLASSES = {"classes": {"REQ-1-1-1": "inherited", "REQ-2-2": "modified", "REQ-3-1": "new"},
           "inherited": ["REQ-1-1-1"], "modified": ["REQ-2-2"], "new": ["REQ-3-1"], "out_of_tree": ["REQ-2-1"]}

def bare_factory(root: Path, continuation: dict, green: list[str] | None = None, specs=((SPEC_A, SPEC_A_TEXT), (SPEC_B, SPEC_B_TEXT))):
    import main as m
    from agent import evolution

    for rel, text in specs:
        write(root, rel, text)
    f = m.Factory.__new__(m.Factory)
    f.project_dir = root
    f.report = {"nodes": {}, "verification": {}}
    f.continuation = continuation
    f.green_specs = list(green or [])
    f.e2e_specs = list(green or [])
    f.partial_green = {}
    registry = evolution.register_specs(root, [rel for rel, _ in specs], json.loads(json.dumps(CLASSES)))
    f.evolution = {"active": True, "classes": json.loads(json.dumps(CLASSES)), "implement": ["REQ-2-2", "REQ-3-1"],
                   "registry": registry, "specs": [rel for rel, _ in specs], "tests": {}, "spec_prefix": {},
                   "inherited_debt": {}, "lifecycle": {}, "settled": []}
    return f

@case
def the_baseline_guards_the_passing_tests_of_a_red_inherited_file_one_by_one():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        red_c = "backend/test-e2e/REQ-1-2.spec.js"
        specs = ((SPEC_A, SPEC_A_TEXT), (SPEC_B, SPEC_B_TEXT),
                 (red_c, "test('REQ-1-2: a', async () => {});\ntest('REQ-1-2: b', async () => {});\n"))
        cont = {"baseline": "taken", "protected": [SPEC_A], "earlier": [s for s, _ in specs],
                "baseline_red": {"REQ-2.part1.spec.js": {B_TITLES[1]: "sig"}, "REQ-1-2.spec.js": {"REQ-1-2: not a title of it": "sig"}}}
        f = bare_factory(root, cont, [SPEC_A], specs)
        lines: list[str] = []
        with patched(log=lines.append):
            f.evolution_after_baseline("the start")
        entry = f.partial_green.get(SPEC_B)
        assert entry and entry["titles"] == [B_TITLES[0], B_TITLES[2]] and entry["inherited"] is True, entry
        assert entry["sha1"] == f.spec_digest((root / SPEC_B).read_bytes())
        assert red_c not in f.partial_green and SPEC_A not in f.partial_green, sorted(f.partial_green)
        books = f.evolution["tests"]
        assert books[SPEC_B] == {B_TITLES[0]: "passed", B_TITLES[1]: "failed", B_TITLES[2]: "passed"}, books
        assert set(books[red_c].values()) == {"red file, failing tests unreadable"}, books[red_c]
        assert cont["baseline_open"] is False and cont["baseline_when"] == "the start"
        assert f.evolution_guards() is True

        cont2 = {"baseline": "skipped", "protected": [], "earlier": [SPEC_A, SPEC_B], "baseline_red": {}, "unchecked": [SPEC_A, SPEC_B]}
        g = bare_factory(root / "two", cont2, [], specs)
        with patched(log=lines.append):
            g.evolution_after_baseline("the start")
        assert cont2["baseline_open"] is True and g.partial_green == {} and g.report["continuation"]["baseline_open"] is True, cont2
        assert any("NOT judged" in line for line in lines), lines[-3:]

@case
def a_modified_leafs_batch_holds_its_unverified_inherited_files_and_releases_them():
    other = "backend/test-e2e/REQ-0.spec.js"
    third = "backend/test-e2e/REQ-9.spec.js"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cont = {"baseline": "taken", "protected": [other, SPEC_B, SPEC_A], "baseline_red": {}}
        f = bare_factory(root, cont, [other, SPEC_B, SPEC_A, third])
        entry = {"batch": "inherited baseline (the start)", "titles": [B_TITLES[0], B_TITLES[1]], "sha1": "x", "written": [], "inherited": True}
        f.partial_green = {SPEC_B: dict(entry)}
        lines: list[str] = []
        with patched(log=lines.append):
            assert f.evolution_hold({"id": "REQ-3-1", "members": ["REQ-3-1"]}) is None
            assert cont["protected"] == [other, SPEC_B, SPEC_A] and f.green_specs == [other, SPEC_B, SPEC_A, third]
            held = f.evolution_hold({"id": "REQ-2-2", "members": ["REQ-2-2"]})
            assert held, lines[-3:]
            assert SPEC_B not in cont["protected"] and SPEC_B not in f.green_specs, (cont["protected"], f.green_specs)
            assert f.partial_green[SPEC_B]["titles"] == [B_TITLES[0]], f.partial_green
            assert SPEC_A in cont["protected"] and SPEC_A in f.green_specs
            f.evolution_release(held)
        assert sorted(cont["protected"]) == sorted([other, SPEC_B, SPEC_A]), cont["protected"]
        assert f.green_specs == [other, SPEC_B, SPEC_A, third], f.green_specs
        assert f.partial_green[SPEC_B] == entry, f.partial_green

        f.partial_green = {SPEC_B: dict(entry, titles=[B_TITLES[1]])}
        with patched(log=lines.append):
            held = f.evolution_hold({"id": "REQ-2-2", "members": ["REQ-2-2"]})
            assert SPEC_B not in f.partial_green
            f.evolution_release(held)
        assert f.partial_green[SPEC_B]["titles"] == [B_TITLES[1]]
        f.evolution_release(None)

def settle_with(root: Path, output_ok: bool, output: str) -> tuple:
    from agent.verify import StepResult

    cont = {"baseline": "taken", "protected": [SPEC_A, SPEC_B], "baseline_red": {}}
    f = bare_factory(root, cont, [SPEC_A, SPEC_B])
    runs: list[list[str]] = []

    def fake_e2e(project_dir, specs, log=print, until=None, **kw):
        runs.append(list(specs))
        return StepResult(output_ok, output)

    lines: list[str] = []
    with patched(log=lines.append, run_e2e=fake_e2e,
                 build_frontend=lambda project_dir, log=print, deadline=None: StepResult(True, "")):
        f.evolution_settle({"id": "REQ-2-2", "members": ["REQ-2-2"]})
    return f, cont, runs, lines

def classes_of(f) -> dict[str, str]:
    return {t["title"]: t["class"] for t in f.evolution["registry"]["files"][SPEC_B]["tests"]}

@case
def settling_supersedes_the_modified_leafs_failing_tests_and_books_other_breaks_as_debt():
    import main as m

    name = Path(SPEC_B).name
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        out = v.World.red_output(name, [B_TITLES[1]])
        assert m.failed_test_titles(out) == {name: {B_TITLES[1]}}, m.failed_test_titles(out)

        f, cont, runs, lines = settle_with(root / "a", False, out)
        assert runs == [[SPEC_B]], runs
        assert classes_of(f) == {B_TITLES[0]: "valid", B_TITLES[1]: "superseded", B_TITLES[2]: "valid"}, classes_of(f)
        assert SPEC_B not in cont["protected"] and SPEC_B not in f.green_specs and SPEC_B not in f.e2e_specs
        assert SPEC_A in cont["protected"] and SPEC_A in f.green_specs
        entry = f.partial_green.get(SPEC_B)
        assert entry and entry["titles"] == [B_TITLES[0], B_TITLES[2]] and entry["inherited"] is True, entry
        assert f.evolution["inherited_debt"] == {}, f.evolution["inherited_debt"]
        assert f.evolution["registry"]["files"][SPEC_B]["modified_leaves"] == []
        assert f.evolution["tests"][SPEC_B] == {B_TITLES[0]: "passed", B_TITLES[1]: "failed", B_TITLES[2]: "passed"}
        assert f.evolution["registry"]["counts"]["superseded"] == 1 and f.evolution["settled"][0]["batch"] == "REQ-2-2"

        f, cont, runs, lines = settle_with(root / "b", False, v.World.red_output(name, [B_TITLES[0], B_TITLES[1]]))
        assert classes_of(f) == {B_TITLES[0]: "valid", B_TITLES[1]: "superseded", B_TITLES[2]: "valid"}, classes_of(f)
        assert f.evolution["inherited_debt"] == {SPEC_B: {"by": "REQ-2-2", "failed": [B_TITLES[0]]}}, f.evolution["inherited_debt"]
        assert f.partial_green[SPEC_B]["titles"] == [B_TITLES[2]], f.partial_green
        assert SPEC_B not in cont["protected"]

        f, cont, runs, lines = settle_with(root / "c", False, "Error: the server did not start")
        assert runs == [[SPEC_B]] and classes_of(f)[B_TITLES[1]] == "unverified", classes_of(f)
        assert cont["protected"] == [SPEC_A, SPEC_B] and f.green_specs == [SPEC_A, SPEC_B] and f.partial_green == {}
        assert f.evolution["settled"] == [] and any("without readable tests" in line for line in lines), lines[-2:]

        f, cont, runs, lines = settle_with(root / "d", True, "1 of 1 spec files passed")
        assert set(classes_of(f).values()) == {"valid"} and cont["protected"] == [SPEC_A, SPEC_B] and f.partial_green == {}

        g = bare_factory(root / "e", {"baseline": "taken", "protected": [SPEC_A, SPEC_B]}, [SPEC_A, SPEC_B])
        with patched(log=lambda line: None, run_e2e=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no run"))):
            g.evolution_settle({"id": "REQ-3-1", "members": ["REQ-3-1"]})

@case
def a_baseline_not_taken_is_taken_after_a_green_batch_at_most_twice():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cont = {"baseline": "build failed", "baseline_open": True, "earlier": [SPEC_A, SPEC_B], "protected": [], "baseline_red": {}}
        f = bare_factory(root, cont)
        calls: list[int] = []

        def taken():
            calls.append(1)
            cont.update(baseline="taken", protected=[SPEC_A, SPEC_B], baseline_red={}, unchecked=[])
            return cont

        f.continuation_baseline = taken
        lines: list[str] = []
        with patched(log=lines.append):
            f.evolution_deferred_baseline({"id": "REQ-2-2"}, ok=False)
            assert calls == [] and cont["baseline_open"] is True and "baseline_deferred_attempts" not in cont
            f.evolution_deferred_baseline({"id": "REQ-2-2"}, ok=True)
            assert calls == [1] and cont["baseline_open"] is False and cont["baseline_when"] == "after REQ-2-2", cont
            assert f.evolution["tests"][SPEC_B] == {t: "passed" for t in B_TITLES}
            f.evolution_deferred_baseline({"id": "REQ-3-1"}, ok=True)
            assert calls == [1]

        cont2 = {"baseline": "crashed: RuntimeError", "baseline_open": True, "earlier": [SPEC_A], "protected": [], "baseline_red": {}}
        g = bare_factory(root / "two", cont2)
        tries: list[int] = []

        def still_failing():
            tries.append(1)
            cont2.update(baseline="build failed")
            return cont2

        g.continuation_baseline = still_failing
        with patched(log=lines.append):
            for batch in ("REQ-2-2", "REQ-3-1", "REQ-4-1"):
                g.evolution_deferred_baseline({"id": batch}, ok=True)
        assert tries == [1, 1] and cont2["baseline_deferred_attempts"] == 2 and cont2["baseline_open"] is True, cont2

@case
def inherited_specs_are_put_back_unless_updated_explicitly():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = bare_factory(root, {"baseline": "taken", "protected": []})
        f._frozen_inherited_specs = {SPEC_A: SPEC_A_TEXT, SPEC_B: SPEC_B_TEXT}
        lines: list[str] = []
        with patched(log=lines.append):
            assert f.evolution_restore_inherited_specs("nothing") == []
            write(root, SPEC_B, "// rewritten by a description pass")
            (root / SPEC_A).unlink()
            assert sorted(f.evolution_restore_inherited_specs("batch REQ-2-2")) == sorted([SPEC_A, SPEC_B])
            assert (root / SPEC_B).read_text(encoding="utf-8") == SPEC_B_TEXT and (root / SPEC_A).read_text(encoding="utf-8") == SPEC_A_TEXT
            updated = SPEC_B_TEXT + "// the stated control type" + NL
            assert f.evolution_update_inherited_spec(SPEC_B, updated, "the requirement states a link") is True
            assert (root / SPEC_B).read_text(encoding="utf-8") == updated and f._frozen_inherited_specs[SPEC_B] == updated
            assert f.evolution["spec_updates"] == [{"file": "REQ-2.part1.spec.js", "why": "the requirement states a link"}]
            write(root, SPEC_B, "// touched again")
            assert f.evolution_restore_inherited_specs("the description pass") == [SPEC_B]
            assert (root / SPEC_B).read_text(encoding="utf-8") == updated
            assert f.evolution_update_inherited_spec("backend/test-e2e/REQ-3-1.evo.spec.js", "x", "not inherited") is False

HANDOFF_TREE = {"id": "ROOT", "children": [
    {"id": "REQ-1", "name": "Identity", "children": [{"id": "REQ-1-1-1", "name": "Sign in"}, {"id": "REQ-1-1-2", "name": "Sign up"}]},
    {"id": "REQ-2", "name": "Repositories", "children": [{"id": "REQ-2-2", "name": "Star a repository"}]}]}

def final_state_factory(root: Path) -> SimpleNamespace:
    classes = {"classes": {"REQ-1-1-1": "inherited", "REQ-1-1-2": "inherited", "REQ-2-2": "modified"},
               "inherited": ["REQ-1-1-1", "REQ-1-1-2"], "modified": ["REQ-2-2"], "new": [], "out_of_tree": ["REQ-2-1", "REQ-9-1"],
               "names": {"REQ-2-1": "Repository list", "REQ-9-1": "Wiki", "REQ-1-1-1": "Sign in"},
               "earlier_status": {"REQ-2-1": "green", "REQ-9-1": "red"}, "unverified": {}}
    registry = {"files": {
        SPEC_A: {"tests": [{"title": "REQ-1-1-1: signs in", "leaf": "REQ-1-1-1", "class": "valid"}]},
        SPEC_B: {"tests": [{"title": B_TITLES[0], "leaf": "REQ-2-1", "class": "valid"},
                           {"title": B_TITLES[1], "leaf": "REQ-2-2", "class": "superseded", "why": "fails after REQ-2-2's change"},
                           {"title": B_TITLES[2], "leaf": "REQ-2-2", "class": "valid"}]}},
        "counts": {"valid": 3, "unverified": 0, "superseded": 1}}
    evolution = {"active": True, "classes": classes, "registry": registry,
                 "tests": {SPEC_A: {"REQ-1-1-1: signs in": "passed"}, SPEC_B: {B_TITLES[0]: "passed", B_TITLES[1]: "failed", B_TITLES[2]: "passed"}},
                 "spec_prefix": {"REQ-2-2": "REQ-2-2.evo"}, "inherited_debt": {}, "lifecycle": {}, "settled": []}
    return SimpleNamespace(
        contract=json.dumps({"pages": [{"route": "/repos", "file": "frontend/src/pages/ReposPage.tsx"}]}),
        report={"nodes": {"REQ-2-2": {"status": "passed", "tests": ["backend/test-e2e/REQ-2-2.evo.spec.js"]}}},
        batch_members={"REQ-2-2": ["REQ-2-2"]}, failed_members=[], unattempted_members=[], description_specs=[],
        open_findings={}, continuation={"baseline": "taken", "baseline_when": "the start", "baseline_open": False, "baseline_red": {}},
        evolution=evolution,
        final_suite={"judged": True, "ok": False, "specs": {"REQ-2-2.evo.spec.js": "red", "REQ-1-1-1.spec.js": "green",
                                                           "REQ-2.part1.spec.js": "green"},
                     "failed_titles": {"REQ-2-2.evo.spec.js": ["REQ-2-2: a second press removes the star"]}},
        project_dir=root)

@case
def the_handoff_is_written_from_the_final_state_with_the_whole_history():
    from agent import handoff as h

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = final_state_factory(root)
        records = {r["id"]: r for r in h.leaf_records(HANDOFF_TREE, f)}
        assert list(records) == ["REQ-1-1-1", "REQ-1-1-2", "REQ-2-2", "REQ-2-1", "REQ-9-1"], list(records)
        assert all(r["status"] != "not attempted" for r in records.values()), {k: r["status"] for k, r in records.items()}
        for leaf_id in ("REQ-1-1-1", "REQ-1-1-2"):
            assert records[leaf_id]["status"] == "inherited" and not records[leaf_id].get("outside_requirement"), records[leaf_id]
        assert records["REQ-1-1-1"]["specs"] == ["REQ-1-1-1.spec.js"] and records["REQ-1-1-1"]["tests"]["passed"] == 1
        for leaf_id in ("REQ-2-1", "REQ-9-1"):
            assert records[leaf_id]["outside_requirement"] is True and records[leaf_id]["status"] == "inherited", records[leaf_id]
        assert records["REQ-9-1"]["earlier_status"] == "red" and records["REQ-2-1"]["name"] == "Repository list"
        built = records["REQ-2-2"]
        assert built["status"] == "red" and built["batch_status"] == "green" and built["status_from"] == "final suite", built
        assert built["specs"] == ["REQ-2-2.evo.spec.js"] and built["round_class"] == "modified", built
        data = h.build_handoff(f, HANDOFF_TREE, "sha-evo", "", root)
        assert [r["id"] for r in data["inventory"]] == ["REQ-1-1-1", "REQ-1-1-2", "REQ-2-2", "REQ-2-1", "REQ-9-1"], data["inventory"]
        assert [r["id"] for r in data["inventory"] if r.get("outside_requirement")] == ["REQ-2-1", "REQ-9-1"]
        assert data["leaf_ids"] == ["REQ-1-1-1", "REQ-1-1-2", "REQ-2-2"], data["leaf_ids"]
        evo = data["evolution"]
        assert evo["modified"] == ["REQ-2-2"] and evo["inherited"] == 2 and evo["outside_requirement"] == ["REQ-2-1", "REQ-9-1"], evo
        assert [t["title"] for t in evo["superseded_tests"]] == [B_TITLES[1]] and evo["spec_names"] == {"REQ-2-2": "REQ-2-2.evo"}
        assert data["final_suite"] == {"ok": False, "specs": f.final_suite["specs"]}
        assert [d["id"] for d in data["debt"]["leaves"]] == ["REQ-2-2"], data["debt"]
        assert data["debt"]["baseline_open"] is False and data["debt"]["inherited_tests_broken"] == {}

        from agent import evolution
        inventory = evolution.historical_inventory(HANDOFF_TREE, data, None)
        assert set(inventory["leaves"]) == {"REQ-1-1-1", "REQ-1-1-2", "REQ-2-2", "REQ-2-1", "REQ-9-1"}, inventory["leaves"]

@case
def the_checkpoint_carries_the_evolution_state_and_the_frozen_specs():
    import main as m

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "app"
        (out / ".arc").mkdir(parents=True)
        dict_fields = ("spec_context", "file_specs", "test_snapshot", "test_ids_by_file", "test_titles", "report", "seed_checks")

        def fresh(sha: str):
            f = m.Factory.__new__(m.Factory)
            f.project_dir, f.revised_specs, f.requirement_sha = out, set(), sha
            return f

        first = fresh("sha-evo")
        for field in m.Factory.CHECKPOINT_FIELDS:
            setattr(first, field, {} if field in dict_fields else [])
        first.started, first.spent_before = time.time(), 0.0
        state = {"active": True, "classes": dict(CLASSES), "implement": ["REQ-2-2", "REQ-3-1"],
                 "registry": {"files": {SPEC_B: {"tests": [{"title": B_TITLES[1], "leaf": "REQ-2-2", "class": "superseded"}]}}},
                 "tests": {SPEC_B: {B_TITLES[1]: "failed"}}, "spec_prefix": {"REQ-2-2": "REQ-2-2.evo"}, "inherited_debt": {},
                 "settled": [{"batch": "REQ-2-2", "files": ["x"]}]}
        first.evolution = state
        first._frozen_inherited_specs = {SPEC_A: SPEC_A_TEXT, SPEC_B: SPEC_B_TEXT}
        first.continuation = {"earlier": [SPEC_A, SPEC_B], "protected": [SPEC_A], "baseline": "taken", "evolution": True}
        with patched(log=lambda line: None):
            first.save_checkpoint("digest-1", ["REQ-2-2"], "")
            second = fresh("sha-evo")
            assert second.load_checkpoint("digest-1") == ["REQ-2-2"]
            third = fresh("sha-other")
            assert third.load_checkpoint("digest-1") == []
        assert second.evolution == state and second.evolution_state() is not None, second.__dict__.get("evolution")
        assert second._frozen_inherited_specs == {SPEC_A: SPEC_A_TEXT, SPEC_B: SPEC_B_TEXT}
        assert second.continuation["evolution"] is True and second.spec_prefix_of({"id": "REQ-2-2", "spec_prefix": "REQ-2-2.evo"}) == "REQ-2-2.evo"
        assert "evolution" not in third.__dict__ and "_frozen_inherited_specs" not in third.__dict__

@case
def the_contract_note_allows_stated_changes_and_takes_a_lead():
    from agent import continuation as c

    app = {"routes": ["/repos -> ReposPage"], "api": ["GET /api/repos (backend/src/routes/repos.js)"], "tables": ["repos(id, name)"],
           "pages": ["frontend/src/pages/ReposPage.tsx"], "names": ['button "Star"'], "visits": ["/repos"], "seeds": []}
    specs = [SPEC_A]
    default = c.contract_note(app, specs)
    assert default.startswith(c.CONTRACT_HEAD) and "edited only to add" not in default, default[:300]
    assert "Change existing code where a stated change" in default, default[:900]
    led = c.contract_note(app, specs, lead="X")
    assert led.split(NL)[0] == "X" and "/repos -> ReposPage" in led, led[:200]
    assert c.architect_block(app, specs, lead="Y").split(NL)[0] == "Y"
    assert c.architect_block(app, specs).startswith("EXISTING APPLICATION.")
    with tempfile.TemporaryDirectory() as tmp:
        assert c.outsider_note(Path(tmp), app, specs, lead="Z").split(NL)[0] == "Z"
        assert c.outsider_note(Path(tmp), app, specs).startswith(c.OUTSIDER_HEAD)

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
    print(f"{len(CASES) - failed}/{len(CASES)} self-tests passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
