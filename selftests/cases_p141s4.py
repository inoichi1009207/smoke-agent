from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CASES: list = []
QUIET = lambda message: None

def case(function):
    CASES.append(function)
    return function

CONTRACT = json.dumps({"pages": [
    {"route": "/signin", "requirements": ["REQ-1-1-1"]},
    {"route": "/orgs/:org", "requirements": ["REQ-2-1-1"]},
    {"route": "/orgs/:org/people", "requirements": ["REQ-2-2-3"]},
    {"route": "/orgs/:org/teams", "requirements": ["REQ-2-2-1", "REQ-2-2-2"]},
    {"route": "/orgs/:org/settings", "requirements": []},
    {"route": "/:owner/:repo", "requirements": ["REQ-3-1"]},
    {"route": "/:owner/:repo/issues", "requirements": ["REQ-5-1-1"]},
    {"route": "/:owner/:repo/pulls", "requirements": ["REQ-1-3"]},
    {"route": "/new", "requirements": ["REQ-4"]},
    {"route": "/explore", "requirements": ["REQ-6-1"]},
    {"route": "/organizations", "requirements": ["REQ-1-2"]},
]})
MEMBERS = ["REQ-2-1-1"]
AUTHORED = {"REQ-1-1-1", "REQ-1-2", "REQ-1-3", "REQ-2-1-1", "REQ-4-2-1"}

def later():
    from agent.audit import later_route_test
    return later_route_test(CONTRACT, MEMBERS, AUTHORED)

def f(where: str, problem: str = "blank page", layer: str = "scripted") -> dict[str, str]:
    return {"where": where, "requirement": "(pages must load without errors)", "problem": problem, "fix": "fix it", "layer": layer}

@case
def finding_routes_reads_route_lists_and_model_paths():
    from agent.audit import finding_routes
    assert finding_routes("route /orgs/acme-demo/people") == ["/orgs/acme-demo/people"]
    assert finding_routes("route /a, /b (reached by clicking “Budget” on /)") == ["/a", "/b"]
    assert finding_routes("/Acme Demo/acme-docs/issues/1 (and /issu") == ["/Acme Demo/acme-docs/issues/1"]
    for where in ("frontend/src/pages/HomePage.tsx:66", "application sources", "control role", "REQ-2-2-1: link “New team”",
                  "route //evil.example", "backend seed data", ""):
        assert finding_routes(where) == [], where

@case
def a_page_only_later_batches_build_is_later():
    is_later = later()
    assert is_later("/orgs/acme-demo/people") and is_later("/orgs/acme-demo/teams")
    assert is_later("/Acme%20Demo/acme-docs/issues") and is_later("/acme-demo/acme-docs")
    assert is_later("/explore?tab=repos")

@case
def own_earlier_unmatched_and_unattributed_pages_are_not_later():
    is_later = later()
    assert not is_later("/orgs/acme-demo")
    assert not is_later("/signin")
    assert not is_later("/organizations")
    assert not is_later("/acme-demo/acme-docs/pulls")
    assert not is_later("/new")
    assert not is_later("/orgs/acme-demo/settings")
    assert not is_later("/nothing/like/this/at/all")
    assert not is_later("/")

@case
def the_most_specific_route_decides():
    from agent.audit import later_route_test

    assert not later()("/orgs/acme-demo")
    contract = json.dumps({"pages": [{"route": "/:a/:b/:c", "requirements": ["REQ-1-1-1"]},
                                     {"route": "/orgs/:org/people", "requirements": ["REQ-9-1"]}]})
    assert later_route_test(contract, MEMBERS, AUTHORED)("/orgs/x/people")
    assert not later_route_test(contract, MEMBERS, AUTHORED)("/x/y/z")

@case
def a_contract_that_is_not_json_suppresses_nothing():
    from agent.audit import later_route_test
    assert not later_route_test('{"pages": [{"route": "/x", "requirements": ["REQ-9"]} ,, }', MEMBERS, set())("/x")
    assert not later_route_test("", MEMBERS, set())("/x")

@case
def split_later_needs_every_named_route_later():
    from agent.audit import split_later_findings
    is_later = later()
    findings = [f("route /orgs/acme-demo/people"), f("route /orgs/acme-demo/people, /signin"), f("route /orgs/acme-demo"),
                f("frontend/src/pages/OrgPeoplePage.tsx:3"), f("route /orgs/acme-demo/teams (reached by clicking “Teams” on /)"),
                f("/acme-demo/acme-docs/issues/1 (and /issues/2)", layer="model")]
    report, logged = split_later_findings(findings, is_later)
    assert [x["where"] for x in logged] == ["route /orgs/acme-demo/people", "route /orgs/acme-demo/teams (reached by clicking “Teams” on /)"], logged
    assert [x["where"] for x in report] == ["route /orgs/acme-demo/people, /signin", "route /orgs/acme-demo",
                                            "frontend/src/pages/OrgPeoplePage.tsx:3", "/acme-demo/acme-docs/issues/1 (and /issues/2)"], report

@case
def authored_ids_come_from_the_specs_on_disk():
    from agent.audit import authored_requirement_ids
    with tempfile.TemporaryDirectory() as tmp:
        assert authored_requirement_ids(Path(tmp)) == set()
        folder = Path(tmp) / "backend" / "test-e2e"
        folder.mkdir(parents=True)
        (folder / "REQ-2-1-1.spec.js").write_text("test('REQ-2-1-1: browse', async () => {});\n// see [REQ-1.2.3]\n", encoding="utf-8")
        (folder / "DESC-REQ-6-6.spec.js").write_text("test('DESC-REQ-6-6: x', () => {});", encoding="utf-8")
        (folder / "notes.txt").write_text("REQ-9-9-9", encoding="utf-8")
        ids = authored_requirement_ids(Path(tmp))
        assert {"REQ-2-1-1", "REQ-1.2.3", "REQ-6-6"} <= ids and "REQ-9-9-9" not in ids and "REQ-2-1" not in ids, ids

@case
def routes_to_snapshot_put_later_only_pages_last():
    from agent.audit import routes_from_contract
    contract = json.dumps({"pages": [{"route": "/explore", "requirements": ["REQ-6-1"]}, {"route": "/signin", "requirements": ["REQ-1-1-1"]},
                                     {"route": "/orgs", "requirements": ["REQ-2-1-1"]}, {"route": "/organizations", "requirements": ["REQ-1-2"]}]})
    assert routes_from_contract(contract, MEMBERS) == ["/", "/orgs", "/explore", "/signin", "/organizations"]
    assert routes_from_contract(contract, MEMBERS, is_later=later()) == ["/", "/orgs", "/signin", "/organizations", "/explore"]

def run_audit(snapshot, found, use_llm=False, llm=None, specs=("REQ-1-1-1", "REQ-2-1-1")):
    from agent import audit
    saved = (audit.take_snapshot, audit.deterministic_findings)
    logs: list[str] = []
    try:
        audit.take_snapshot = lambda *a, **k: snapshot
        audit.deterministic_findings = lambda *a, **k: [dict(x) for x in found]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "backend" / "test-e2e"
            folder.mkdir(parents=True)
            for spec in specs:
                (folder / f"{spec}.spec.js").write_text(f"test('{spec}: x', async () => {{}});\n", encoding="utf-8")
            node = {"id": "REQ-2-1-1", "members": MEMBERS, "name": "Browse Organization Repositories",
                    "description": "The organization page presents “Repositories” as a navigation link."}
            out = audit.audit_node(llm, Path(tmp), node, CONTRACT, log=logs.append, use_llm=use_llm)
    finally:
        audit.take_snapshot, audit.deterministic_findings = saved
    return out, logs

@case
def audit_node_logs_later_only_findings_and_reports_the_rest():
    found = [f("route /orgs/acme-demo/people"), f("route /orgs/acme-demo/teams"), f("route /signin", "console error"),
             f("route /orgs/acme-demo", "wrong role"), f("frontend/src/pages/X.tsx:4", "raw visibility")]
    out, logs = run_audit([{"route": "/", "aria": ""}], found)
    assert [x["where"] for x in out] == ["route /signin", "route /orgs/acme-demo", "frontend/src/pages/X.tsx:4"], out
    assert any("2 finding(s) on pages only later batches build" in line for line in logs), logs
    assert any("(later) route /orgs/acme-demo/people" in line for line in logs), logs

    out, _ = run_audit([{"route": "/", "aria": ""}], found, specs=("REQ-1-1-1", "REQ-2-1-1", "REQ-2-2-3"))
    assert "route /orgs/acme-demo/people" in [x["where"] for x in out] and "route /orgs/acme-demo/teams" not in [x["where"] for x in out]

class FakeLLM:
    def __init__(self, findings):
        self.findings = findings
        self.messages = None

    def chat(self, messages, **kwargs):
        self.messages = messages
        return {"content": json.dumps({"findings": self.findings})}

@case
def model_findings_on_later_pages_are_logged_and_crawled_pages_say_what_was_recorded():
    model = [{"where": "/orgs/acme-demo/people", "requirement": "presents “Repositories” as a navigation link",
              "problem": "the people page is blank", "fix": "build it"},
             {"where": "/orgs/acme-demo", "requirement": "presents “Repositories” as a navigation link",
              "problem": "no Repositories link", "fix": "add it"}]
    llm = FakeLLM(model)
    snapshot = [{"route": "/", "url": "http://x/", "aria": "- heading", "text": "Home"},
                {"route": "/orgs/acme-demo/people", "crawled": True, "console": []},
                {"route": "/acme-demo/acme-docs", "crawled": True, "console": [], "error": "Timeout 10000ms exceeded"}]
    out, logs = run_audit(snapshot, [], use_llm=True, llm=llm)
    assert [x["where"] for x in out] == ["/orgs/acme-demo"], out
    assert any("(later) /orgs/acme-demo/people" in line for line in logs), logs
    prompt = llm.messages[1]["content"]
    assert "ERRORS: \n" not in prompt and "ERRORS: " not in prompt, prompt
    assert "reached by following the pages' links" in prompt and prompt.count("console errors: none recorded") == 2, prompt
    assert "ERROR while loading: Timeout 10000ms exceeded" in prompt, prompt

def run_recheck(repaired, fresh, snapshot=(("route", "/"),), build_ok=True, detaudit_logs=()):
    from agent import audit, verify
    saved = (audit.take_snapshot, audit.deterministic_findings, verify.build_frontend)
    logs: list[str] = []
    calls = {"build": 0, "snapshot": 0}

    class Step:
        ok = build_ok

    def build(*a, **k):
        calls["build"] += 1
        return Step()

    def snap(*a, **k):
        calls["snapshot"] += 1
        return None if snapshot is None else [dict(snapshot)]

    def det(project_dir, node, snap_, log=print):
        for line in detaudit_logs:
            log(line)
        return [dict(x) for x in fresh]

    try:
        verify.build_frontend, audit.take_snapshot, audit.deterministic_findings = build, snap, det
        with tempfile.TemporaryDirectory() as tmp:
            result = audit.recheck_repaired(Path(tmp), {"id": "REQ-5-1-2", "members": ["REQ-5-1-2"]}, CONTRACT, repaired, log=logs.append)
    finally:
        audit.take_snapshot, audit.deterministic_findings, verify.build_frontend = saved
    return result, logs, calls

@case
def the_recheck_logs_which_repaired_findings_are_gone():
    fixed = f("frontend/src/pages/SearchPage.tsx:41", "renders {repo.visibility} raw")
    kept = f("frontend/src/pages/HomePage.tsx:66", "renders {repo.visibility} raw")
    model = f("/Acme Demo/acme-docs/issues/1", "fails to render", layer="model")

    result, logs, calls = run_recheck([fixed, kept, model], [f("frontend/src/pages/HomePage.tsx:70", "renders {repo.visibility} raw"),
                                                               f("route /x", "another finding")])
    assert result["ran"] and [x["where"] for x in result["gone"]] == [fixed["where"]], result
    assert [x["where"] for x in result["still"]] == [kept["where"]] and result["unchecked"] == [model], result
    assert calls == {"build": 1, "snapshot": 1}, calls
    assert any("1 of 2 repaired finding(s) gone, 1 still there; 1 of the model auditor's not re-checked" in line for line in logs), logs
    assert any("still there: frontend/src/pages/HomePage.tsx:66" in line for line in logs), logs

@case
def a_recheck_that_could_not_run_calls_nothing_gone():
    repaired = [f("route /a", "blank page")]
    for kwargs, why in ((dict(snapshot=None), "no snapshot"), (dict(build_ok=False), "does not build"),
                        (dict(detaudit_logs=["[audit] interaction probes: application did not start"]), "did not run"),
                        (dict(detaudit_logs=["[audit] visual checks crashed: RuntimeError('boom')"]), "did not run")):
        result, logs, _ = run_recheck(repaired, [], **kwargs)
        assert not result["ran"] and result["gone"] == [] and result["still"] == [], (kwargs, result)
        assert any(why in line and "not re-checked" in line for line in logs), (kwargs, logs)

@case
def only_model_findings_start_no_recheck():
    result, logs, calls = run_recheck([f("/x", "claim", layer="model")], [])
    assert calls == {"build": 0, "snapshot": 0} and not result["ran"], calls
    assert any("no scripted finding to re-check" in line for line in logs), logs

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
        except Exception:
            failed += 1
            print(f"FAIL {function.__name__}")
            traceback.print_exc()
    print(f"{len(CASES) - failed}/{len(CASES)} p141s4 cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
