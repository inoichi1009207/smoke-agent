from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.dont_write_bytecode = True

CASES = []

def case(function):
    CASES.append(function)
    return function

NEW_KEYS = ("outsider_enabled", "v162b_crossdep", "v162b_dependency_probe", "stated_region_scope", "signin_page_by_form",
            "session_revoke_after_batch")

def keys_read_in_code() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in [ROOT / "main.py"] + sorted((ROOT / "agent").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        readers, owners, consts = {"setting_on", "setting_seconds"}, {"SETTINGS"}, {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("settings"):
                for alias in node.names:
                    if alias.name in ("setting_on", "setting_seconds"):
                        readers.add(alias.asname or alias.name)
                    if alias.name == "SETTINGS":
                        owners.add(alias.asname or alias.name)
            if isinstance(node, ast.FunctionDef) and node.name.startswith("_setting"):
                readers.add(node.name)
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                consts.update({t.id: node.value.value for t in node.targets if isinstance(t, ast.Name)})
        for node in ast.walk(tree):
            key = None
            if isinstance(node, ast.Call):
                f = node.func
                name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
                owner = f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else ""
                args = list(node.args)[1:] if name == "_setting" else list(node.args)
                reads = ((isinstance(f, ast.Name) and name in readers) or (isinstance(f, ast.Attribute) and name in ("setting_on", "setting_seconds"))
                         or (name == "get" and owner in owners))
                if reads and args:
                    a = args[0]
                    key = a.value if isinstance(a, ast.Constant) else consts.get(a.id) if isinstance(a, ast.Name) else None
            elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id in owners \
                    and isinstance(node.slice, ast.Constant):
                key = node.slice.value
            if isinstance(key, str):
                found.setdefault(key, []).append(f"{path.name}:{node.lineno}")
    return found

@case
def every_settings_key_read_in_code_is_in_the_defaults():
    from agent import settings

    found = keys_read_in_code()
    assert len(found) > 100, len(found)
    for key in NEW_KEYS + ("seed_foundation_staged", "audit_enabled", "llm_thinking_stream", "description_pass_hubs"):
        assert key in found, key
    missing = {k: v for k, v in found.items() if k not in settings._DEFAULTS}
    assert not missing, f"read in code, missing from agent/settings.py _DEFAULTS (AGENT_<KEY> cannot override them): {missing}"
    for key in NEW_KEYS:
        assert settings._DEFAULTS[key] is (key != "session_revoke_after_batch"), key

@case
def agent_env_false_turns_each_new_switch_off():
    env = dict(os.environ, **{"AGENT_" + k.upper(): "false" for k in NEW_KEYS})
    code = ("import sys; sys.dont_write_bytecode = True; from agent import settings as s; "
            f"print([s.setting_on(k) for k in {list(NEW_KEYS)!r}])")
    out = subprocess.run([sys.executable, "-B", "-c", code], cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    assert out.stdout.strip() == str([False] * len(NEW_KEYS)), out.stdout
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_")}
    out = subprocess.run([sys.executable, "-B", "-c", code], cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)
    assert out.stdout.strip() == str([k != "session_revoke_after_batch" for k in NEW_KEYS]), (out.stdout, out.stderr[-400:])

SESSION = {"where": "session_revoke probe (two browser sessions, live)", "requirement": "(revoking a session ends it)",
           "problem": "the revoked session still answers 200", "fix": "refuse it"}
SCRIPTED = {"where": "route /", "requirement": "The home page shows Explore.", "problem": "Explore is not shown",
            "fix": "show it", "layer": "scripted"}

def drive(session_on: bool, probe_findings, recheck=None, scripted=(), repaired=True, yaml_present=True):
    import cases_p141s3 as s3
    import cases_rollback as rb
    from agent import audit, session_chain

    calls: dict[str, list] = {"hard_fits": [], "probe": [], "recheck": [], "scripted_recheck": []}
    saved = (session_chain.batch_findings, audit.recheck_session_findings, audit.recheck_repaired)
    with tempfile.TemporaryDirectory() as req:
        if yaml_present:
            (Path(req) / "requirements.yaml").write_text("id: ROOT\n", encoding="utf-8")

        def tweak(f):
            f.evolution_state = lambda: {"active": True}
            f.requirements_dir = Path(req)
            real = f.hard_fits
            f.hard_fits = lambda what, *a, **k: (calls["hard_fits"].append(what) or True) if "session" in what else real(what, *a, **k)

        session_chain.batch_findings = lambda project_dir, whole, members, **k: (calls["probe"].append(list(members))
                                                                                  or [dict(x) for x in probe_findings])
        audit.recheck_session_findings = lambda project_dir, whole, members, found, log=print: (
            calls["recheck"].append([dict(x) for x in found]) or recheck(found))
        audit.recheck_repaired = lambda project_dir, node, contract, found, log=print: (
            calls["scripted_recheck"].append([dict(x) for x in found]) or {"ran": True, "gone": list(found), "still": [], "unchecked": []})
        script = [{"write": {rb.APP: "OWN-OK"}}]
        if (session_on and probe_findings) or scripted:
            script.append({"write": {rb.APP: "OWN-OK audited" if repaired else "own spec broken by audit"}})
        try:
            f, lines, after, passed, _ = s3.run_debt_batch(
                script, tweak=tweak,
                settings={"audit_enabled": True, "audit_llm": "never", "evolution_data_lifecycle": False,
                          "session_revoke_after_batch": session_on},
                patch={"audit_node": lambda llm, project_dir, node, contract, log=print, use_llm=False: [dict(x) for x in scripted]})
        finally:
            session_chain.batch_findings, audit.recheck_session_findings, audit.recheck_repaired = saved
    return f, lines, calls

def fp(finding):
    from agent.audit import finding_fingerprint

    return finding_fingerprint(finding)

@case
def the_session_hook_checks_its_switch_first():
    f, lines, calls = drive(False, [SESSION], yaml_present=False)
    assert calls["hard_fits"] == [] and calls["probe"] == [], calls
    assert not any("crashed" in l for l in lines), [l for l in lines if "crashed" in l]
    f, lines, calls = drive(True, [], yaml_present=True)
    assert calls["hard_fits"] == ["the session revoke probe"] and calls["probe"] == [["REQ-9"]], calls

@case
def a_session_finding_closes_only_when_its_probe_says_gone():
    gone = lambda found: {"ran": True, "gone": list(found), "still": [], "unchecked": []}
    still = lambda found: {"ran": True, "gone": [], "still": list(found), "unchecked": []}
    unchecked = lambda found: {"ran": False, "gone": [], "still": [], "unchecked": list(found)}

    f, lines, calls = drive(True, [SESSION], gone, scripted=[SCRIPTED])
    assert [x["problem"] for x in calls["recheck"][0]] == [SESSION["problem"]], calls["recheck"]
    assert [x["problem"] for x in calls["scripted_recheck"][0]] == [SCRIPTED["problem"]], calls["scripted_recheck"]
    assert fp(SESSION) in f.audit_sent_fingerprints and fp(SCRIPTED) in f.audit_sent_fingerprints
    assert fp(SESSION) not in f.open_findings and fp(SCRIPTED) not in f.open_findings
    assert "the session revoke re-check" in calls["hard_fits"], calls["hard_fits"]

    f, lines, calls = drive(True, [], gone, scripted=[SCRIPTED])
    assert fp(SCRIPTED) in f.audit_sent_fingerprints and fp(SCRIPTED) not in f.open_findings and calls["recheck"] == []
    assert [x["problem"] for x in calls["scripted_recheck"][0]] == [SCRIPTED["problem"]], calls
    f, lines, calls = drive(True, [], gone, scripted=[SCRIPTED], repaired=False)
    assert fp(SCRIPTED) in f.open_findings and fp(SCRIPTED) not in (f.__dict__.get("audit_sent_fingerprints") or [])

    f, lines, calls = drive(True, [SESSION], unchecked)
    assert fp(SESSION) not in (f.__dict__.get("audit_sent_fingerprints") or []) and fp(SESSION) in f.open_findings
    assert calls["scripted_recheck"] == [], calls

    f, lines, calls = drive(True, [SESSION], still)
    assert fp(SESSION) not in (f.__dict__.get("audit_sent_fingerprints") or []) and fp(SESSION) in f.open_findings
    assert fp(SESSION) in (f.__dict__.get("audit_rolled_back_fingerprints") or [])

    def crash(found):
        raise RuntimeError("probe died")

    f, lines, calls = drive(True, [SESSION], crash)
    assert fp(SESSION) not in (f.__dict__.get("audit_sent_fingerprints") or []) and fp(SESSION) in f.open_findings
    assert any("re-check of the session-revoke finding(s) crashed" in l for l in lines), lines

    f, lines, calls = drive(True, [SESSION], gone, repaired=False)
    assert calls["recheck"] == [] and fp(SESSION) not in (f.__dict__.get("audit_sent_fingerprints") or [])

@case
def recheck_session_findings_over_the_real_batch_findings():
    from agent import audit, session_chain

    saved = (session_chain.revoke_wanted, session_chain.revoke_probe)
    from agent import settings as _settings
    _settings.SETTINGS["session_revoke_after_batch"] = True
    probe = {}
    session_chain.revoke_wanted = lambda text: True
    session_chain.revoke_probe = lambda project_dir, whole, **k: dict(probe)
    try:
        def run(res, found):
            probe.clear()
            probe.update(res)
            lines: list[str] = []
            return audit.recheck_session_findings(Path("."), "", ["REQ-1"], found, log=lines.append), lines

        probe.update({"status": "finding", "result": "finding: revoke finding",
                      "findings": ["the revoked session still answers 200 Fix: refuse it"]})
        original = [dict(x, source=audit.SESSION_SOURCE) for x in session_chain.batch_findings(Path("."), "", ["REQ-1"], log=lambda *_: None)]
        assert len(original) == 1 and audit.is_session_finding(original[0])
        out, _ = run({"status": "clean", "result": "clean: revoke clean, revoked_page clean", "findings": []}, original)
        assert out["ran"] and [x["problem"] for x in out["gone"]] == [original[0]["problem"]] and not out["unchecked"], out
        out, _ = run({"status": "clean", "result": "clean: revoke clean (revoked_page not run: no built frontend)", "findings": []}, original)
        assert not out["gone"] and len(out["unchecked"]) == 1, out
        out, _ = run({"status": "not run", "result": "not run: no revoke route", "findings": []}, original)
        assert not out["ran"] and not out["gone"] and len(out["unchecked"]) == 1, out
        out, _ = run({"status": "finding", "result": "finding: revoke finding",
                      "findings": ["the revoked session still answers 200 Fix: refuse it"]}, original)
        assert out["ran"] and len(out["still"]) == 1 and not out["gone"] and not out["unchecked"], out
        session_chain.revoke_wanted = lambda text: False
        out, _ = run({"status": "clean", "result": "clean: revoke clean, revoked_page clean", "findings": []}, original)
        assert not out["ran"] and not out["gone"] and len(out["unchecked"]) == 1, out
        session_chain.revoke_wanted = lambda text: (_ for _ in ()).throw(RuntimeError("x"))
        out, _ = run({}, original)
        assert not out["gone"] and len(out["unchecked"]) == 1, out
    finally:
        session_chain.revoke_wanted, session_chain.revoke_probe = saved

    lines: list[str] = []
    res = audit.recheck_repaired(Path("."), {"id": "REQ-1"}, "", [dict(SESSION)], log=lines.append)
    assert res == {"ran": False, "gone": [], "still": [], "unchecked": [SESSION]}, res

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
