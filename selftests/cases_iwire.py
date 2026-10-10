from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CASES = []
QUIET = lambda message: None

def case(function):
    CASES.append(function)
    return function

NODE = {"id": "REQ-2-1", "name": "Branches", "description": "Pick a branch.",
        "requirement_text": 'The "Switch branch" button opens a "Find a branch" textbox. Pressing Escape closes it.'}
SNAPSHOT = [{"route": "/", "url": "http://127.0.0.1:3300/", "links": [], "aria": '- button "Switch branch"'}]
FOUND = {"where": "route /", "requirement": "(the requirement) opens", "problem": "open on load", "fix": "close it"}

def audit(stand_in, logs: list | None = None) -> list[dict[str, str]]:
    import agent.detaudit as detaudit

    original = detaudit.interaction_findings
    detaudit.interaction_findings = stand_in
    try:
        with tempfile.TemporaryDirectory() as tmp:
            return detaudit.deterministic_findings(Path(tmp), NODE, SNAPSHOT, log=(logs.append if logs is not None else QUIET))
    finally:
        detaudit.interaction_findings = original

@case
def the_audit_passes_the_node_text_to_the_probes_and_reports_their_findings():
    seen = []

    def stand_in(text, project_dir, snapshot, log=print):
        seen.append((text, snapshot))
        return [dict(FOUND)]

    found = audit(stand_in)
    assert seen == [(NODE["requirement_text"], SNAPSHOT)], seen
    assert [f for f in found if f.get("problem") == "open on load"] == [FOUND], found

@case
def the_probe_findings_are_capped_per_check():
    import agent.detaudit as detaudit

    found = audit(lambda *a, **k: [dict(FOUND, where=f"route /{i}") for i in range(30)])
    assert len([f for f in found if f.get("problem") == "open on load"]) == detaudit.MAX_FINDINGS_PER_CHECK

@case
def a_crashing_probe_is_logged_and_reports_nothing():
    def stand_in(*args, **kwargs):
        raise RuntimeError("boom")

    logs: list = []
    found = audit(stand_in, logs)
    assert not [f for f in found if f.get("problem") == "open on load"]
    assert any("interaction probes crashed" in str(line) for line in logs), logs

@case
def without_a_browser_the_real_probes_report_nothing():

    found = audit(__import__("agent.interaction_probes", fromlist=["x"]).interaction_findings)
    assert not [f for f in found if "interaction" in str(f.get("requirement", "")).lower()], found

def visual_audit(stand_in, logs: list | None = None) -> list[dict[str, str]]:
    import agent.detaudit as detaudit

    original = detaudit.visual_findings
    detaudit.visual_findings = stand_in
    try:
        with tempfile.TemporaryDirectory() as tmp:
            return detaudit.deterministic_findings(Path(tmp), NODE, SNAPSHOT, log=(logs.append if logs is not None else QUIET))
    finally:
        detaudit.visual_findings = original

@case
def the_audit_passes_the_snapshot_to_the_visual_checks_and_caps_them():
    import agent.detaudit as detaudit

    seen = []

    def stand_in(text, snapshot, log=print):
        seen.append((text, snapshot))
        return [dict(FOUND, problem="raw ISO", where=f"route /{i}") for i in range(30)]

    found = visual_audit(stand_in)
    assert seen == [(NODE["requirement_text"], SNAPSHOT)], seen
    assert len([f for f in found if f.get("problem") == "raw ISO"]) == detaudit.MAX_FINDINGS_PER_CHECK

@case
def a_crashing_visual_check_is_logged_and_reports_nothing():
    def stand_in(*args, **kwargs):
        raise RuntimeError("boom")

    logs: list = []
    found = visual_audit(stand_in, logs)
    assert not [f for f in found if f.get("problem") == "raw ISO"]
    assert any("visual checks crashed" in str(line) for line in logs), logs
