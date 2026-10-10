from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

KEY = "findings_owner_batch_v2"
CASES = []
MISSING = object()

def case(function):
    CASES.append(function)
    return function

class Settings:
    def __init__(self, **values):
        self.values = values

    def __enter__(self):
        from agent.settings import SETTINGS

        self.saved = {key: SETTINGS.get(key, MISSING) for key in self.values}
        SETTINGS.update(self.values)

    def __exit__(self, *exc):
        from agent.settings import SETTINGS

        for key, value in self.saved.items():
            if value is MISSING:
                SETTINGS.pop(key, None)
            else:
                SETTINGS[key] = value

class Logs:

    def __enter__(self):
        import main as m

        self.lines: list[str] = []
        self.saved = m.log
        m.log = lambda line: self.lines.append(str(line))
        return self

    def __exit__(self, *exc):
        import main as m

        m.log = self.saved

def finding(where: str, problem: str, owners=None, **extra) -> dict:
    out = {"where": where, "requirement": "(stated path)", "problem": problem, "fix": "fix it", "layer": "scripted", **extra}
    if owners is not None:
        out["owner_leaf"] = list(owners)
    return out

def bare_factory():
    import main as m

    f = m.Factory.__new__(m.Factory)
    f.open_findings = {}
    f.report = {"nodes": {}, "verification": {}}
    f.failed_members, f.unattempted_members = [], []
    return f

QUEUE = [({"id": "A", "members": ["L-A"]}, 0), ({"id": "B", "members": ["L-B1", "L-B2"]}, 0), ({"id": "C", "members": ["L-C"]}, 0)]

def mark_at(f, index: int, done: list[str] | None = None) -> None:
    node = QUEUE[index][0]
    f.held_queue_mark(node, [q for q in QUEUE[index + 1:]], list(done or []))

@case
def a_structured_owner_of_a_later_batch_is_held_and_not_repaired_now():
    with Settings(**{KEY: True}), Logs() as logs:
        f = bare_factory()
        mark_at(f, 0)
        later = finding("outsider-walk: rendered app (route /settings)", "step 3 “Active sessions” missing", ["L-B1"])
        now = finding("route /", "blank page")
        sent = f.hold_for_owner_batches("A", QUEUE[0][0], [later, now])
        assert sent == [now], sent
        assert [r["problem"] for r in f.held_findings["L-B1"]] == [later["problem"]]
        assert any(line.startswith("[audit] held for L-B1: 1 finding(s)") for line in logs.lines), logs.lines

@case
def owners_current_done_unknown_or_mixed_are_sent_now():
    with Settings(**{KEY: True}):
        f = bare_factory()
        mark_at(f, 1, done=["A"])
        own = finding("x", "own batch", ["L-B2"])
        done = finding("x", "a done batch", ["L-A"])
        unknown = finding("x", "a leaf of no batch of this run (inherited)", ["REQ-OLD-1"])
        mixed = finding("x", "a later and a done owner", ["L-C", "L-A"])
        mixed_now = finding("x", "a later and the current owner", ["L-C", "L-B1"])
        later = finding("x", "only later", ["L-C"])
        sent = f.hold_for_owner_batches("B", QUEUE[1][0], [own, done, unknown, mixed, mixed_now, later])
        assert sent == [own, done, unknown, mixed, mixed_now], [s["problem"] for s in sent]
        assert list(f.held_findings) == ["L-C"]

@case
def prose_naming_a_later_req_id_without_an_owner_is_sent_now():
    with Settings(**{KEY: True}):
        f = bare_factory()
        mark_at(f, 0)
        prose = {"where": "REQ-L-B1 sign-in", "requirement": "“REQ-L-B1” L-B1 must stay enabled",
                 "problem": "the sign-in button is disabled because the L-B1 / REQ-L-B1 configuration is missing",
                 "fix": "enable it", "layer": "scripted"}
        assert f.hold_for_owner_batches("A", QUEUE[0][0], [prose]) == [prose]
        assert not (f.__dict__.get("held_findings") or {})

@case
def nothing_ahead_or_last_batch_sends_everything():
    with Settings(**{KEY: True}):
        f = bare_factory()
        mark_at(f, 2, done=["A", "B"])
        later = finding("x", "owner of a done batch", ["L-B1"])
        assert f.hold_for_owner_batches("C", QUEUE[2][0], [later]) == [later]
        g = bare_factory()
        assert g.hold_for_owner_batches("C", QUEUE[2][0], [later]) == [later]

@case
def a_crash_while_holding_sends_everything():
    import agent.finding_owner as fo

    with Settings(**{KEY: True}), Logs() as logs:
        f = bare_factory()
        mark_at(f, 0)
        saved = fo.split_for_owner_batches
        fo.split_for_owner_batches = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            items = [finding("x", "later", ["L-C"])]
            assert f.hold_for_owner_batches("A", QUEUE[0][0], items) == items
        finally:
            fo.split_for_owner_batches = saved
        assert any("crashed, all sent now" in line for line in logs.lines)

@case
def the_owner_batch_gets_every_held_claim_listed_with_a_count():
    with Settings(**{KEY: True}), Logs() as logs:
        f = bare_factory()
        mark_at(f, 0)
        many = [finding(f"route /p{i}", f"problem number {i} " + "x" * 900, ["L-B2"]) for i in range(25)]
        f.hold_for_owner_batches("A", QUEUE[0][0], many)
        note = f.held_delivery_note("B", QUEUE[1][0])
        assert note.startswith("AUDIT CLAIMS HELD FOR THIS BATCH (25, all listed)"), note[:120]
        for i in range(25):
            assert f"{i + 1}/25. [route /p{i}]" in note and f"problem number {i} " in note, i
        assert "[audit] delivered 25 held finding(s) to B" in logs.lines
        assert ("x" * 900) in note

        f.held_release_into("B", [])
        f.held_after_batch(QUEUE[1][0], True)
        assert not f.held_findings and not f.open_findings and not f.__dict__.get("held_released")
        assert not any("handed to closing" in line for line in logs.lines)

@case
def a_finding_of_two_later_batches_settled_by_the_first_goes_no_further():
    with Settings(**{KEY: True}):
        f = bare_factory()
        mark_at(f, 0)
        both = finding("x", "named by B and C", ["L-B1", "L-C"])
        f.hold_for_owner_batches("A", QUEUE[0][0], [both])
        mark_at(f, 1, ["A"])
        assert f.held_delivery_note("B", QUEUE[1][0])
        f.held_release_into("B", [])
        f.held_after_batch(QUEUE[1][0], True)
        assert f.held_delivery_note("C", QUEUE[2][0]) == "" and not f.held_findings

@case
def a_crashed_audit_never_settles_a_held_finding():
    with Settings(**{KEY: True}):
        f = bare_factory()
        mark_at(f, 0)
        both = finding("x", "named by B and C", ["L-B1", "L-C"])
        f.hold_for_owner_batches("A", QUEUE[0][0], [both])
        mark_at(f, 1, ["A"])
        assert f.held_delivery_note("B", QUEUE[1][0])
        f.__dict__.setdefault("held_audit_crashed", set()).add("B")
        f.held_release_into("B", [])
        f.held_after_batch(QUEUE[1][0], True)
        assert "named by B and C" in f.held_delivery_note("C", QUEUE[2][0])

@case
def a_red_first_owner_leaves_the_finding_held_for_the_other_owner():
    with Settings(**{KEY: True}):
        f = bare_factory()
        mark_at(f, 0)
        both = finding("x", "named by B and C", ["L-B1", "L-C"])
        f.hold_for_owner_batches("A", QUEUE[0][0], [both])
        mark_at(f, 1, ["A"])
        assert f.held_delivery_note("B", QUEUE[1][0])
        f.held_release_into("B", [])
        f.held_after_batch(QUEUE[1][0], False)
        assert "named by B and C" in f.held_delivery_note("C", QUEUE[2][0])
        assert not f.__dict__.get("held_released")

@case
def a_red_owner_batch_releases_them_to_the_next_audit():
    with Settings(**{KEY: True}), Logs() as logs:
        f = bare_factory()
        mark_at(f, 0)
        held = finding("x", "later", ["L-B1"])
        f.hold_for_owner_batches("A", QUEUE[0][0], [held])
        mark_at(f, 1, ["A"])
        f.held_release_into("B", [])
        f.held_after_batch(QUEUE[1][0], False)
        assert not f.held_findings and f.held_released == [held]
        own = finding("y", "C's own", ["L-C"])
        joined = f.held_release_into("C", [own])
        assert joined == [own, held] and not f.held_released
        assert any("1 released held finding(s) join this audit's findings" in line for line in logs.lines)
        f.held_after_batch(QUEUE[2][0], True)
        f.held_hand_to_closing()
        assert not f.open_findings

@case
def a_kept_owner_batch_still_reporting_it_releases_it_unless_a_kept_repair_took_it():
    from agent.audit import finding_fingerprint

    with Settings(**{KEY: True}):
        for repaired in (False, True):
            f = bare_factory()
            f.audit_sent_fingerprints = []
            mark_at(f, 0)
            held = finding("x", "still there", ["L-B1"])
            f.hold_for_owner_batches("A", QUEUE[0][0], [held])
            mark_at(f, 1, ["A"])
            f.held_release_into("B", [dict(held)])
            if repaired:
                f.audit_sent_fingerprints = [finding_fingerprint(held)]
            f.held_after_batch(QUEUE[1][0], True)
            assert bool(f.__dict__.get("held_released")) is (not repaired), (repaired, f.__dict__.get("held_released"))

@case
def a_kept_owner_batch_without_an_audit_releases_them():
    with Settings(**{KEY: True}):
        f = bare_factory()
        mark_at(f, 0)
        held = finding("x", "no audit to say it is gone", ["L-B1"])
        f.hold_for_owner_batches("A", QUEUE[0][0], [held])
        mark_at(f, 1, ["A"])
        f.held_after_batch(QUEUE[1][0], True)
        assert f.held_released == [held]

@case
def released_and_never_sent_go_to_the_closing_with_their_full_text():
    with Settings(**{KEY: True}), Logs() as logs:
        f = bare_factory()
        mark_at(f, 0)
        long_problem = "the control is missing " + "y" * 700
        held = finding("route /x", long_problem, ["L-B1"])
        f.hold_for_owner_batches("A", QUEUE[0][0], [held])
        mark_at(f, 1, ["A"])
        f.held_after_batch(QUEUE[1][0], False)
        f.held_hand_to_closing()
        entry = list(f.open_findings.values())[0]
        assert entry["problem"] == long_problem and entry["where"] == "route /x" and entry["fix"] == "fix it", entry
        assert f.report["held_unrun"][0]["problem"] == long_problem
        assert any(line.startswith("[audit] 1 held finding(s) handed to closing") for line in logs.lines)

class QueueWorld:

    def __init__(self, stop_before: str | None = None):
        self.f = f = bare_factory()
        self.notes: dict[str, str] = {}
        popped = iter([str(n["id"]) for n, _ in QUEUE])
        f.over_budget = lambda: next(popped, None) == stop_before
        f.hard_end = lambda: None
        f.foundation_retry_due = lambda *a, **k: False
        f.evolution_after_batch = lambda node, ok: None
        f.save_checkpoint = lambda *a, **k: None

        f.snapshot_sources = lambda: {}
        f.restore_sources = lambda snapshot: 0

        def attempt_node(node, nodes_left, pause_sec=60.0, requeue=False, retry=False):
            self.notes[str(node["id"])] = f.held_delivery_note(str(node["id"]), node)
            if node["id"] == "A":
                found = [finding("x", "for B", ["L-B2"]), finding("y", "for C", ["L-C"]), finding("z", "for A itself", ["L-A"])]
                f.hold_for_owner_batches("A", node, f.held_release_into("A", found))
            else:
                f.held_release_into(str(node["id"]), [])
            return True

        f.attempt_node = attempt_node

    def run(self) -> bool:
        return self.f.run_queue([(n, []) for n, _ in QUEUE], [], "digest", "")

@case
def run_queue_delivers_to_the_owner_batch_and_holds_nothing_at_the_end():
    with Settings(**{KEY: True}), Logs() as logs:
        world = QueueWorld()
        assert world.run() is True
        assert world.notes["A"] == ""
        assert "1/1." in world.notes["B"] and "for B" in world.notes["B"] and "for C" not in world.notes["B"]
        assert "for C" in world.notes["C"]
        assert not world.f.held_findings and not world.f.open_findings
        assert not any("handed to closing" in line for line in logs.lines)

@case
def an_owner_batch_cut_by_the_budget_leaves_its_findings_to_the_closing():
    with Settings(**{KEY: True}), Logs() as logs:
        world = QueueWorld(stop_before="C")
        assert world.run() is False
        assert "for B" in world.notes["B"] and "C" not in world.notes
        assert not world.f.held_findings, world.f.held_findings
        assert [v["batch"] for v in world.f.open_findings.values()] == ["held for L-C"], world.f.open_findings
        assert [v["problem"] for v in world.f.open_findings.values()] == ["for C"]
        assert [r["problem"] for r in world.f.report["held_unrun"]] == ["for C"]
        assert any(line.startswith("[audit] 1 held finding(s) handed to closing") for line in logs.lines), logs.lines
        assert world.f.unattempted_members == ["L-C"]

def checkpoint_factory(root: Path):
    import cases_rollback as rb

    f = rb.make_factory(root, "APP")
    for name in f.CHECKPOINT_FIELDS:
        if not hasattr(f, name):
            setattr(f, name, [] if name.endswith("s") else {})
    f.description_pass, f.contract, f.partial_green = {}, "", {}
    return f

@case
def held_findings_survive_a_checkpoint_and_are_delivered_after_the_resume():
    import json

    with Settings(**{KEY: True}), Logs() as logs, tempfile.TemporaryDirectory() as tmp:
        f = checkpoint_factory(Path(tmp))
        f.open_findings = {}
        mark_at(f, 0)
        f.hold_for_owner_batches("A", QUEUE[0][0], [finding("route /s", "held across a restart", ["L-B1"])])
        f.save_checkpoint("digest", ["A"], "")
        g = checkpoint_factory(Path(tmp))
        os.environ.pop("AGENT_RESUME", None)
        assert g.load_checkpoint("digest") == ["A"]
        assert [r["problem"] for r in g.held_findings["L-B1"]] == ["held across a restart"]
        assert any("held audit finding row(s) restored" in line for line in logs.lines)
        assert "held across a restart" in g.held_delivery_note("B", QUEUE[1][0])
        state = json.loads((Path(tmp) / ".arc" / "resume.json").read_text(encoding="utf-8"))
        assert "held_findings" in state

@case
def switch_off_checkpoints_and_restores_nothing_new():
    import json

    with Settings(**{KEY: False}), tempfile.TemporaryDirectory() as tmp:
        f = checkpoint_factory(Path(tmp))
        f.held_findings = {"L-B1": [finding("x", "y", ["L-B1"])]}
        f.save_checkpoint("digest", ["A"], "")
        state = json.loads((Path(tmp) / ".arc" / "resume.json").read_text(encoding="utf-8"))
        assert "held_findings" not in state
        state["held_findings"] = {"L-B1": [finding("x", "y", ["L-B1"])]}
        (Path(tmp) / ".arc" / "resume.json").write_text(json.dumps(state), encoding="utf-8")
        g = checkpoint_factory(Path(tmp))
        os.environ.pop("AGENT_RESUME", None)
        g.load_checkpoint("digest")
        assert "held_findings" not in g.__dict__

@case
def switch_off_holds_nothing_tags_nothing_and_delivers_nothing():
    import agent.finding_owner as fo

    with Settings(**{KEY: False}):
        f = bare_factory()
        mark_at(f, 0)
        assert "held_ahead" not in f.__dict__
        items = [finding("x", "later", ["L-C"])]
        assert f.hold_for_owner_batches("A", QUEUE[0][0], items) == items
        assert f.held_delivery_note("C", QUEUE[2][0]) == ""
        assert fo.tag({"where": "w"}, ["L-C"]) == {"where": "w"}
        f.held_findings = {"L-C": items}
        f.held_after_batch(QUEUE[2][0])
        assert f.held_findings == {"L-C": items} and not f.open_findings

@case
def every_call_site_in_main_is_behind_the_switch():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    for call in ("self.hold_for_owner_batches(req_id, node, audit_findings)", "self.held_delivery_note(req_id, node)",
                 'state["held_findings"] = '):
        line = next(l for l in source.splitlines() if call in l)
        index = source.index(line)
        window = source[max(0, index - 200): index + len(line)]
        assert f'setting_on("{KEY}", True)' in window, call

    for method in ("def held_queue_mark", "def held_delivery_note", "def held_after_batch"):
        body = source[source.index(method): source.index(method) + 1400]
        assert "finding_owner.enabled()" in body, method

@case
def the_outsider_walk_tags_the_scenario_leaf_and_every_leaf_stating_the_missing_control():
    from agent import outsider_walk as ow

    result = {"walks": [{"account": "evo-owner", "paths": [
        {"key": "0:0", "status": "missing", "route": "/settings", "done": ["Account menu", "Settings"],
         "missing": "Active sessions", "visible": ["Profile"], "late": [], "leaf": "L-B1", "scenario": "S1",
         "quote": "Account menu → Settings → Active sessions", "steps": []},
        {"key": "1:0", "status": "missing", "route": "/", "done": [], "missing": "Settings", "visible": [], "late": [],
         "leaf": "L-C", "scenario": "S2", "quote": "Settings", "steps": []}]}]}
    stating = {"Active sessions": {"L-B1"}, "Settings": {"L-C", "L-A"}}
    with Settings(**{KEY: True}):
        found = ow._path_findings(result, stating)
        by_missing = {("Active sessions" if "Active sessions" in f["problem"] and "step 3" in f["problem"] else "Settings"): f for f in found}
        assert by_missing["Active sessions"]["owner_leaf"] == ["L-B1"], found
        assert by_missing["Settings"]["owner_leaf"] == ["L-A", "L-C"]
        assert all("owner_leaf" not in f for f in ow._path_findings(result, None))
    with Settings(**{KEY: False}):
        assert all("owner_leaf" not in f for f in ow._path_findings(result, stating))

@case
def the_stated_message_check_names_every_leaf_stating_the_message():
    import cases_v150b as vb
    from agent import stated_messages as sm

    yaml_text = vb.yaml_requirement([
        ("L-A", "The form has a “Create thing A” button.", [("THEN", "the page displays “Name is required”")]),
        ("L-C", "The editor has a “Save thing C” button.", [("THEN", "the page displays “Name is required”")]),
        ("L-B1", "The form has a “Rename thing B” button.", [("THEN", "the page displays “Name must be 50 characters or fewer”")])])
    items = {i["message"]: i for i in sm.stated_messages(yaml_text)}
    assert items, "no stated messages parsed"
    shared = next(i for m, i in items.items() if "required" in m.lower())
    assert shared["leaf"] == "L-A" and shared["leaves"] == ["L-A", "L-C"], shared
    single = next(i for m, i in items.items() if "50 characters" in m)
    assert single["leaves"] == ["L-B1"]
    with tempfile.TemporaryDirectory() as tmp, Settings(**{KEY: True}):
        root = Path(tmp)
        (root / "frontend" / "src" / "pages").mkdir(parents=True)
        (root / "backend" / "src").mkdir(parents=True)
        (root / "frontend" / "src" / "pages" / "P.tsx").write_text(
            "export const P = () => <div><button>Create thing A</button><button>Rename thing B</button></div>;\n", encoding="utf-8")
        found = sm.stated_message_findings(yaml_text, root, log=lambda *_: None)
        tagged = [f.get("owner_leaf") for f in found]
        assert len(found) == 2, found
        assert all(t in (["L-A", "L-C"], ["L-B1"]) for t in tagged), tagged
        limited = sm.stated_message_findings(yaml_text, root, log=lambda *_: None, leaf_ids=["L-B1"])
        assert all("owner_leaf" not in f for f in limited)

@case
def the_real_github_requirement_owns_the_sessions_walk_finding_by_req_1_4_only():
    path = ROOT.parent / "evolution" / "req" / "hackathon-evolution--github" / "requirements.yaml"
    if not path.is_file():
        print("      (skipped: the github requirement is not on this machine)")
        return
    from agent import outsider_walk as ow

    text = path.read_text(encoding="utf-8")
    with Settings(**{KEY: True}):
        stating = ow._stating_leaves(text, ow._credentials(text))
    assert stating is not None and stating.get("Active sessions") == {"REQ-1-4"}, (stating or {}).get("Active sessions")

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
    print(f"{len(CASES) - failed}/{len(CASES)} v168c hold cases passed in {time.time() - started:.1f}s")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
