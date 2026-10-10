from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

CASES = []

def case(function):
    CASES.append(function)
    return function

STAGE1_TREE = {"id": "ROOT", "children": [
    {"id": "REQ-1", "name": "Identity", "children": [
        {"id": "REQ-1-1-1", "name": "Register"}, {"id": "REQ-1-1-2", "name": "Sign in"}, {"id": "REQ-1-1-3", "name": "Recover"},
        {"id": "REQ-1-2", "name": "Settings"}, {"id": "REQ-1-3", "name": "Sign out"}]}]}
STAGE1_REQUIREMENT = "Seed data: account `alice-dev` with password `Valid-password-123!`. The `Password` field is labelled."
CONTRACT = {
    "pages": [{"route": "/login", "file": "frontend/src/pages/LoginPage.tsx"}, {"route": "/", "file": "frontend/src/pages/HomePage.tsx"}],
    "api": [{"method": "post", "path": "/api/login"}, {"method": "GET", "path": "/api/me"}],
    "seed": [{"entity": "user", "fields": {"username": "alice-dev", "email": "alice@example.test", "password": "Valid-password-123!"}}],
    "naming": ["The sign-in button is named `Sign in`; the `Password` field keeps its label", '"Forgot password", "Username or email"'],
    "notes": ["Landing page after sign-in is the workspace at /", "EXISTING APPLICATION (delivered ...): keep it all",
              "Stored under D:\\work\\run\\backend\\data and /tmp/arc/x"],
}

def stage1_factory(root: Path):
    return SimpleNamespace(
        contract=json.dumps(CONTRACT),
        report={"nodes": {"REQ-1-1-1": {"status": "passed", "tests": ["backend/test-e2e/REQ-1-1-1.spec.js", "backend/tests/x.test.js"]},
                          "REQ-1-1-2": {"status": "gave_up", "tests": ["backend/test-e2e/REQ-1-1-2.spec.js"]},
                          "REQ-1-1-3": {"status": "budget", "tests": ["backend/test-e2e/REQ-1-1-3.spec.js"]},
                          "REQ-1-2": {"status": "budget", "tests": [], "put_back_members": ["REQ-1-2"]}}},
        batch_members={"REQ-1-1-1": ["REQ-1-1-1"], "REQ-1-1-2": ["REQ-1-1-2"], "REQ-1-1-3": ["REQ-1-1-3"], "REQ-1-2": ["REQ-1-2"], "REQ-1-3": ["REQ-1-3"]},
        failed_members=["REQ-1-1-2", "REQ-1-1-3", "REQ-1-2"], unattempted_members=["REQ-1-3"],
        description_specs=["backend/test-e2e/DESC-REQ-1-1-1.spec.js"],
        open_findings={"fp1": {"class": "SQL column missing", "where": "backend/src/routes/auth.js:91", "batch": "REQ-1-1-2"}},
        continuation=None, project_dir=root)

@case
def scrubbing_removes_passwords_and_local_paths_and_keeps_labels():
    from agent import handoff as h

    secrets = h.secrets_of(STAGE1_REQUIREMENT, CONTRACT)
    assert "Valid-password-123!" in secrets and "Password" not in secrets, secrets
    out = json.dumps(h.scrub(CONTRACT, secrets, ["D:\\work\\run"]))
    assert "Valid-password-123!" not in out and h.PASSWORD_TEXT in out
    assert "D:\\\\work" not in out and "/tmp/arc" not in out and "<local path>" in out, out
    assert "`Password` field" in out and "Sign in" in out
    assert '\\"Forgot password\\", \\"Username or email\\"' in out, out

@case
def the_handoff_holds_status_specs_contract_seed_conventions_and_debt():
    from agent import handoff as h

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".arc" / "design").mkdir(parents=True)
        (root / ".arc" / "design" / "contract.json").write_text("{}", encoding="utf-8")
        data = h.build_handoff(stage1_factory(root), STAGE1_TREE, "sha-1", STAGE1_REQUIREMENT, root)
        status = {leaf["id"]: leaf["status"] for leaf in data["leaves"]}
        assert status == {"REQ-1-1-1": "green", "REQ-1-1-2": "red", "REQ-1-1-3": "budget", "REQ-1-2": "put back",
                          "REQ-1-3": "not attempted"}, status
        assert data["leaf_ids"] == list(status) and data["requirement_sha256"] == "sha-1"
        first = data["leaves"][0]
        assert first["specs"] == ["REQ-1-1-1.spec.js", "DESC-REQ-1-1-1.spec.js"], first
        assert data["promised"] == {"routes": ["/login", "/"], "pages": ["frontend/src/pages/LoginPage.tsx", "frontend/src/pages/HomePage.tsx"],
                                    "api": ["POST /api/login", "GET /api/me"]}, data["promised"]
        assert data["seed"] == [{"entity": "user", "names": {"username": "alice-dev", "email": "alice@example.test",
                                                             "password": h.PASSWORD_TEXT}}], data["seed"]
        assert any("workspace" in c for c in data["conventions"]) and not any(c.startswith("EXISTING APPLICATION") for c in data["conventions"])
        assert [d["id"] for d in data["debt"]["leaves"]] == ["REQ-1-1-2", "REQ-1-1-3", "REQ-1-2", "REQ-1-3"]
        assert data["debt"]["open_findings"] == [{"class": "SQL column missing", "where": "backend/src/routes/auth.js:91", "batch": "REQ-1-1-2"}]
        assert data["contract_saved_at"] == ".arc/design/contract.json"
        text = json.dumps(data)
        assert "Valid-password-123!" not in text and str(root) not in text and str(root).replace("\\", "/") not in text
        assert "test(" not in text

@case
def write_handoff_writes_both_files_and_never_raises():
    from agent import handoff as h

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        lines: list[str] = []
        assert h.write_handoff(stage1_factory(root), STAGE1_TREE, "sha-1", STAGE1_REQUIREMENT, root, log=lines.append)
        data = json.loads((root / ".arc-handoff" / "handoff.json").read_text(encoding="utf-8"))
        md = (root / ".arc-handoff" / "HANDOFF.md").read_text(encoding="utf-8")
        assert data["format"] == h.FORMAT and "REQ-1-1-2: red" in md and "Valid-password" not in md, md
        assert any(line.startswith("[handoff] written") for line in lines), lines
        broken = SimpleNamespace(report="not a dict", contract=None)
        lines.clear()
        assert h.write_handoff(broken, STAGE1_TREE, "sha-1", "", root / "other", log=lines.append) is False
        assert lines and "not written" in lines[0], lines
        assert h.write_handoff(stage1_factory(root), None, "sha-1", "", root / "third", log=lines.append) is False
        assert not (root / "third").exists()

@case
def the_factory_writes_only_from_a_run_and_only_when_on_and_after_the_clean_up():
    import main as m
    from agent.settings import SETTINGS

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = m.Factory.__new__(m.Factory)
        f.project_dir = root
        f.clean_runtime_state()
        assert not (root / ".arc-handoff").exists()
        req = root / "req"
        req.mkdir()
        (req / "requirements.yaml").write_text(STAGE1_REQUIREMENT, encoding="utf-8")
        f.requirements_dir, f.handoff_tree, f.requirement_sha = req, STAGE1_TREE, "sha-1"
        for key, value in vars(stage1_factory(root)).items():
            setattr(f, key, value)
        saved = SETTINGS.get("stage_handoff", True)
        try:
            SETTINGS["stage_handoff"] = False
            assert f.write_stage_handoff() is False and not (root / ".arc-handoff").exists()
            SETTINGS["stage_handoff"] = True
            (root / "backend").mkdir()
            (root / "backend" / "database.db").write_text("x", encoding="utf-8")
            f.clean_runtime_state()
        finally:
            SETTINGS["stage_handoff"] = saved
        assert not (root / "backend" / "database.db").exists() and (root / ".arc-handoff" / "handoff.json").is_file()

@case
def open_findings_are_booked_closed_and_checkpointed():
    import main as m

    f = m.Factory.__new__(m.Factory)
    scripted = {"where": "backend/src/routes/a.js:3", "problem": "SQL uses a column the table lacks: x.", "layer": "scripted"}
    model = {"where": "/", "problem": "looks odd", "layer": "model"}
    f.book_open_findings("REQ-2", [scripted, model])
    assert list(f.open_findings.values()) == [{"class": "SQL uses a column the table lacks:", "where": "backend/src/routes/a.js:3", "batch": "REQ-2"}]
    f.close_open_findings([dict(scripted, where="backend/src/routes/a.js:7")])
    assert f.open_findings == {}
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert 'state["open_findings"]' in source and "self.open_findings = dict(state[\"open_findings\"])" in source
    assert "self.book_open_findings(req_id, audit_findings)" in source and "self.close_open_findings(settled)" in source

    assert "settled = [f for f in audit_findings if not is_session_finding(f)] if probed else audit_findings" in source

@case
def load_handoff_takes_only_a_valid_handoff_of_another_requirement():
    from agent import handoff as h

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        lines: list[str] = []
        assert h.load_handoff(root, "sha-2", log=lines.append) is None and "no .arc-handoff" in lines[-1]
        path = root / ".arc-handoff" / "handoff.json"
        path.parent.mkdir()
        path.write_text("{not json", encoding="utf-8")
        assert h.load_handoff(root, "sha-2", log=lines.append) is None and "unreadable" in lines[-1]
        path.write_text(json.dumps({"format": "other"}), encoding="utf-8")
        assert h.load_handoff(root, "sha-2", log=lines.append) is None and "not a handoff" in lines[-1]
        h.write_handoff(stage1_factory(root), STAGE1_TREE, "sha-2", STAGE1_REQUIREMENT, root, log=lines.append)
        assert h.load_handoff(root, "sha-2", log=lines.append) is None and "this same requirement" in lines[-1]
        got = h.load_handoff(root, "sha-3", log=lines.append)
        assert got and got["requirement_sha256"] == "sha-2"
        assert "HANDOFF OF THE EARLIER STAGE" in h.architect_text(got) and "/api/login" in h.architect_text(got)
        assert "workspace" in h.notes_text(got) and "REQ-1-1-2 ended red" in h.notes_text(got)
        assert len(h.notes_text(got)) <= h.NOTES_CAP

@case
def a_batch_depending_on_an_earlier_red_leaf_gets_the_debt_note():
    from agent import handoff as h

    tree = {"id": "ROOT", "children": [{"id": "REQ-3", "dependencies": ["REQ-1"], "children": [
        {"id": "REQ-3-1", "dependencies": ["REQ-1-1-2"]}, {"id": "REQ-3-2"}]},
        {"id": "REQ-4", "children": [{"id": "REQ-4-1", "dependencies": ["REQ-1-1-1"]}]}]}
    debts = {"REQ-1-1-2": {"status": "red", "name": "Sign in"}}
    note = h.debt_note(tree, ["REQ-3-1"], debts)
    assert note.startswith("EARLIER-STAGE DEBT") and "REQ-1-1-2 (Sign in: red)" in note, note
    assert h.debt_note(tree, ["REQ-3-2"], debts)
    assert h.debt_note(tree, ["REQ-4-1"], debts) == "" and h.debt_note(tree, ["REQ-3-1"], {}) == ""

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
    dependencies:
    - REQ-1-1-2
    description: The repository page lists the files.
  - id: REQ-3-2
    name: New repository
    type: ATOMIC
    description: The `New repository` button creates a repository.
"""

@case
def a_continuation_reads_the_handoff_into_the_architect_the_notes_and_the_depending_batch():
    import cases_v150a as v
    from agent import handoff as h

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project = root / "out"
        v.stage1_app(project)
        h.write_handoff(stage1_factory(project), STAGE1_TREE, "stage-1-sha", STAGE1_REQUIREMENT, project, log=lambda line: None)
        lines, seen = v.dry_start(project, v.requirement_dir(root, STAGE2_YAML), set(), {})
        queue = seen["queue"]
        assert queue is not None, lines[-10:]
        assert "HANDOFF OF THE EARLIER STAGE" in (seen["contract_kwargs"] or {}).get("existing", ""), seen["contract_kwargs"]
        notes = json.loads(queue["contract"]).get("notes") or []
        note = next(str(n) for n in notes if str(n).startswith("EXISTING APPLICATION"))
        assert "Conventions of the earlier stage" in note and "REQ-1-1-2 ended red" in note, note[-600:]
        assert any(n.startswith("EARLIER-STAGE DEBT") for n in queue["debt_notes"].values()), queue["debt_notes"]
        assert any("[handoff] the earlier stage's handoff read" in line for line in lines)
        assert queue["continuation"]["handoff"]["requirement_sha256"] == "stage-1-sha"

        (project / ".arc-handoff" / "handoff.json").write_text("{broken", encoding="utf-8")
        (project / ".arc" / "resume.json").unlink(missing_ok=True)
        lines, seen = v.dry_start(project, v.requirement_dir(root / "b", STAGE2_YAML), set(), {})
        assert "HANDOFF OF THE EARLIER STAGE" not in (seen["contract_kwargs"] or {}).get("existing", "")
        assert "EXISTING APPLICATION" in (seen["contract_kwargs"] or {}).get("existing", "")
        assert any("unreadable" in line for line in lines) and not any(seen["queue"]["debt_notes"].values())

@case
def the_fresh_path_never_reads_a_handoff():
    import cases_v150a as v
    from agent import handoff as h

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project = root / "out"
        v.template_copy(project)
        h.write_handoff(stage1_factory(project), STAGE1_TREE, "stage-1-sha", STAGE1_REQUIREMENT, project, log=lambda line: None)
        lines, seen = v.dry_start(project, v.requirement_dir(root, STAGE2_YAML), set(), {})
        assert seen["queue"] is not None and seen["contract_kwargs"] == {} and seen["copy_overwrite"] is True
        assert not any("[handoff]" in line or "[continuation]" in line for line in lines), [l for l in lines if "handoff" in l]
        assert not any(seen["queue"]["debt_notes"].values())

@case
def the_handoff_folder_is_never_read_as_sources_specs_or_seed():
    import cases_v150a as v
    import main as m
    from agent import code_checks, detaudit, duplicate_names, interaction_probes, role_ladder, seed_presence, session_probe
    from agent import continuation as c
    from agent import description_pass as dp
    from agent import stated_controls, stated_messages

    token = "HANDOFFTOKEN"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        v.template_copy(root)
        before = {
            "inputs": detaudit._inputs_digest(root, ("backend", "frontend")),
            "fingerprint": interaction_probes.source_fingerprint(root),
        }
        folder = root / ".arc-handoff"
        folder.mkdir()
        for name in ("handoff.json", "HANDOFF.md", "x.js", "y.tsx", "REQ-9.spec.js", "DESC-REQ-9.spec.js", "seed_db.js", "init_db.js"):
            (folder / name).write_text(f"// {token} SELECT {token}_col FROM users; <button>{token}</button> '{token}-seed'", encoding="utf-8")
        texts = [detaudit.application_sources(root),
                 "\n".join(s.text if hasattr(s, "text") else str(s) for s in code_checks.backend_sources(root)),
                 seed_presence.backend_text(root),
                 "\n".join(str(p) for p in role_ladder.backend_files(root)),
                 json.dumps(session_probe.frontend_login_keys(root)),
                 "\n".join(str(p) for p in duplicate_names._source_files(root)),
                 "\n".join(str(p) for p in stated_controls.iter_sources(root)),
                 json.dumps(stated_messages._collect(root)[1:]),
                 json.dumps(c.existing_app(root, [])),
                 "\n".join(c.sources_beyond_template(root, m.AGENT_ROOT / "template")),
                 "\n".join(c.earlier_specs(root, {"id": "ROOT", "children": []}))]
        for index, text in enumerate(texts):
            assert token not in text and ".arc-handoff" not in text, (index, text[:300])
        f = m.Factory.__new__(m.Factory)
        f.project_dir = root
        assert not any(rel.startswith(".arc-handoff") for rel in f.snapshot_sources())
        run = dp.DescriptionPass.__new__(dp.DescriptionPass)
        run.dir = root
        assert run.desc_files() == []
        assert detaudit._inputs_digest(root, ("backend", "frontend")) == before["inputs"]
        assert interaction_probes.source_fingerprint(root) == before["fingerprint"]

def _flaky_first_world():
    import cases_v150a as v

    class FlakyFirst(v.World):

        def run_e2e(self, project_dir, specs, log=print):
            from agent.verify import StepResult

            text = self.sources()
            if "FLAKY" in text and v.F1 in specs and len(specs) > 1:
                self.runs.append((list(specs), text))
                name = Path(v.F1).name
                return StepResult(False, "\n".join([f"FAILED SPEC FILES: {name}", f"## {name}", self.red_output(name, [v.title(v.F1)])]))
            return v.World.run_e2e(self, project_dir, specs, log=log)

    return FlakyFirst

@case
def a_flaky_earlier_spec_among_the_first_three_does_not_roll_a_batch_back():
    import cases_v150a as v

    world = _flaky_first_world()
    f, w, lines, app, passed = v.run_batch("base", [{"write": {v.APP: "base OWN-OK FLAKY"}, "check": True}], continuation=True,
                                           world_class=world)
    assert app == "base OWN-OK FLAKY" and not v.foundation_put_back(lines), lines[-12:]
    assert any("pass alone on this state" in line and "REQ-1-1-1" in line for line in lines), lines[-12:]
    assert v.F1 in f.green_specs and [v.F1] in [specs for specs, _ in w.runs]

    f, w, lines, app, passed = v.run_batch("base", [{"write": {v.APP: "base OWN-OK FLAKY"}, "check": True}], continuation=False,
                                           world_class=world)
    assert app == "base" and v.foundation_put_back(lines), lines[-12:]
    assert not any("pass alone" in line for line in lines)

@case
def an_earlier_spec_among_the_first_three_that_really_breaks_still_rolls_back():
    import cases_v150a as v

    f, w, lines, app, passed = v.run_batch("base", [{"write": {v.APP: "base OWN-OK " + v.brk(v.F1)}, "check": True}], continuation=True)
    assert app == "base" and v.foundation_put_back(lines) and not passed, lines[-12:]
    assert [v.F1] in [specs for specs, _ in w.runs]

@case
def a_confirmation_that_does_not_fit_the_window_is_unconfirmed():
    import cases_v150a as v
    import main as m

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = v.make_factory(root, "base", True)
        f.confirm_window_end = m.clock.now() - 1.0
        lines: list[str] = []
        saved_log, saved_e2e = m.log, m.run_e2e
        ran: list = []
        try:
            m.log = lines.append
            m.run_e2e = lambda *a, **k: ran.append(a) or (_ for _ in ()).throw(AssertionError("no run past the window"))
            state = {"failed": [Path(v.F1).name], "output": "", "foundation": True}
            assert f.foundation_regression_is_new("REQ-9", {"x": b""}, state) is False
        finally:
            m.log, m.run_e2e = saved_log, saved_e2e
        assert not ran and state["failed"] == [Path(v.F1).name], state
        assert any("unconfirmed" in line for line in lines), lines

@case
def the_baseline_runs_a_red_earlier_spec_once_more():
    import main as m
    from agent.verify import StepResult

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        specs = [f"backend/test-e2e/REQ-1-{i}.spec.js" for i in (1, 2, 3)]
        for rel in specs:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text("// stage 1", encoding="utf-8")
        calls: list[list[str]] = []

        def fake(project_dir, run_specs, log=print, until=None):
            calls.append([Path(s).name for s in run_specs])
            red = [Path(s).name for s in run_specs if s == specs[2] or (s == specs[1] and len(calls) == 1)]
            return StepResult(not red, ("FAILED SPEC FILES: " + ", ".join(red)) if red else "passed")

        f = m.Factory.__new__(m.Factory)
        f.project_dir, f.report, f.green_specs, f.e2e_specs = root, {}, [], []
        f.continuation = {"earlier": list(specs)}
        saved = (m.run_e2e, m.build_frontend, m.ensure_helper, m.log)
        try:
            m.run_e2e, m.build_frontend = fake, (lambda project_dir, log=print, deadline=None: StepResult(True, ""))
            m.ensure_helper, m.log = (lambda project_dir, log=None: None), (lambda line: None)
            cont = f.continuation_baseline()
        finally:
            m.run_e2e, m.build_frontend, m.ensure_helper, m.log = saved
        assert calls == [["REQ-1-1.spec.js", "REQ-1-2.spec.js", "REQ-1-3.spec.js"], ["REQ-1-2.spec.js", "REQ-1-3.spec.js"]], calls
        assert cont["protected"] == specs[:2] and list(cont["baseline_red"]) == ["REQ-1-3.spec.js"] and cont["unchecked"] == [], cont
        assert f.green_specs == specs[:2]

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
