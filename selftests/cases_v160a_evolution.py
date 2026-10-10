from __future__ import annotations

import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import evolution as ev

CASES = []
NL = chr(10)

def case(function):
    CASES.append(function)
    return function

def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

def leaf(leaf_id: str, description: str = "A plain rule.", scenarios: int = 1, deps=()) -> dict:
    return {"id": leaf_id, "name": f"Leaf {leaf_id}", "type": "ATOMIC", "dependencies": list(deps), "description": description,
            "scenarios": [{"name": f"{leaf_id}: S{i}", "steps": [{"keyword": "WHEN", "content": "x"}]} for i in range(1, scenarios + 1)]}

def folder(folder_id: str, *children, description: str = "") -> dict:
    return {"id": folder_id, "name": f"Folder {folder_id}", "type": "FOLDER", "description": description, "children": list(children)}

MODIFIED = ("Original Feature Description" + NL + NL + "The username must consist of lowercase letters, digits or single hyphens. "
            "A duplicate username displays \u201cUsername already exists\u201d." + NL + NL + "Modified Feature Description" + NL + NL
            + "The form keeps all controls from the original feature. The username rule is changed: a username may contain "
            "lowercase letters, digits, single hyphens and single underscores.")

@case
def preservation_needs_sources_beyond_the_template_and_an_earlier_run():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        template, out = root / "template", root / "out"
        write(template, "frontend/src/App.tsx", "starter")
        write(out, "frontend/src/App.tsx", "starter")
        assert ev.preservation(out, template)["preserve"] is False
        write(out, ".arc-handoff/handoff.json", "{}")
        assert ev.preservation(out, template)["preserve"] is False
        write(out, "frontend/src/App.tsx", "the inherited app")
        got = ev.preservation(out, template)
        assert got["preserve"] is True and got["signals"]["beyond"] == 1, got
        (out / ".arc-handoff" / "handoff.json").unlink()
        assert ev.preservation(out, template)["preserve"] is False
        write(out, "backend/test-e2e/REQ-1.spec.js", "test('REQ-1: a', async () => {});")
        assert ev.preservation(out, template)["preserve"] is True

@case
def preservation_is_fail_safe():
    from agent import continuation

    real = continuation.sources_beyond_template

    def boom(*a, **k):
        raise RuntimeError("scan failed")

    continuation.sources_beyond_template = boom
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root / "out", "frontend/src/App.tsx", "inherited")
            got = ev.preservation(root / "out", root / "template")
            assert got["preserve"] is True and "fail-safe" in got["reason"], got
            empty = root / "empty"
            empty.mkdir()
            assert ev.preservation(empty, root / "template")["preserve"] is False
    finally:
        continuation.sources_beyond_template = real

def history_tree() -> dict:
    return {"id": "ROOT", "children": [folder("REQ-1", leaf("REQ-1-1"), leaf("REQ-1-2"), leaf("REQ-1-3")),
                                       folder("REQ-2", leaf("REQ-2-1"))]}

@case
def classes_come_from_the_whole_history():
    old = history_tree()
    tree = {"id": "ROOT", "children": [folder("REQ-1", leaf("REQ-1-1"), leaf("REQ-1-2", MODIFIED), leaf("REQ-1-3", "Changed text."),
                                               leaf("REQ-1-4"))]}
    handoff = {"leaves": [{"id": i, "status": "green" if i != "REQ-2-1" else "put back"} for i in ("REQ-1-1", "REQ-1-2", "REQ-1-3", "REQ-2-1")]}
    inventory = ev.historical_inventory(tree, handoff, old)
    got = ev.classify(tree, inventory, old)
    assert got["inherited"] == ["REQ-1-1"], got
    assert got["modified"] == ["REQ-1-2", "REQ-1-3"] and got["unmarked_changes"] == ["REQ-1-3"], got
    assert got["new"] == ["REQ-1-4"] and got["out_of_tree"] == ["REQ-2-1"], got
    assert got["earlier_status"]["REQ-2-1"] == "put back" and not got["unverified"], got
    assert ev.evolution_round(got) and ev.to_implement(got) == ["REQ-1-2", "REQ-1-3", "REQ-1-4"]

@case
def without_handoff_or_old_requirement_new_is_unverified():
    tree = {"id": "ROOT", "children": [folder("REQ-1", leaf("REQ-1-1"), leaf("REQ-1-2"))]}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write(root, "backend/test-e2e/REQ-1-1.spec.js", 'test("REQ-1-1: a", async () => {});' + NL)
        inventory = ev.historical_inventory(tree, None, None, root, ["backend/test-e2e/REQ-1-1.spec.js"])
        got = ev.classify(tree, inventory)
    assert not inventory["complete"] and got["inherited"] == ["REQ-1-1"] and got["new"] == ["REQ-1-2"], got
    assert "REQ-1-2" in got["unverified"] and "REQ-1-1" in got["unverified"], got

@case
def a_later_stage_is_no_evolution_round():
    stage2 = {"id": "ROOT", "children": [folder("REQ-3", leaf("REQ-3-1"), leaf("REQ-3-2"))]}
    handoff = {"leaves": [{"id": "REQ-1-1", "status": "green"}, {"id": "REQ-1-2", "status": "green"}]}
    got = ev.classify(stage2, ev.historical_inventory(stage2, handoff, None))
    assert got["new"] == ["REQ-3-1", "REQ-3-2"] and got["out_of_tree"] == ["REQ-1-1", "REQ-1-2"], got
    assert not ev.evolution_round(got)

@case
def only_modified_and_new_leaves_are_batched():
    from agent.batching import build_batches

    tree = {"id": "ROOT", "children": [folder("REQ-1", leaf("REQ-1-1"), leaf("REQ-1-2", MODIFIED), leaf("REQ-1-3"),
                                               description="Shared module rules."), folder("REQ-2", leaf("REQ-2-1"))]}
    pruned = ev.implementation_tree(tree, ["REQ-1-2", "REQ-2-1"])
    members = sorted(m for b in build_batches(pruned) for m in b["members"])
    assert members == ["REQ-1-2", "REQ-2-1"], members
    leaves, nodes, _ = ev.tree_index(pruned)
    assert leaves["REQ-1-2"] is ev.tree_index(tree)[0]["REQ-1-2"]
    assert nodes["REQ-1"]["description"] == "Shared module rules." and len(tree["children"][0]["children"]) == 3

@case
def inherited_specs_are_registered_per_test():
    classes = {"classes": {"REQ-4-1": "modified", "REQ-4-2": "inherited"}, "out_of_tree": ["REQ-9-1"], "modified": ["REQ-4-1"]}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write(root, "backend/test-e2e/REQ-4.part1.spec.js", NL.join([
            'test("REQ-4-1: switch a branch - Scenario 1", async ({ page }) => {});',
            "test('REQ-4-2: create a branch - Scenario 1', async ({ page }) => {});"]))
        write(root, "backend/test-e2e/REQ-9-1.spec.js", 'test("REQ-9-1: old feature", async () => {});')
        write(root, "backend/test-e2e/helper-flow.spec.js", 'test("signs in", async () => {});')
        specs = ["backend/test-e2e/REQ-4.part1.spec.js", "backend/test-e2e/REQ-9-1.spec.js", "backend/test-e2e/helper-flow.spec.js"]
        reg = ev.register_specs(root, specs, classes, None)
    files = reg["files"]
    mixed = files["backend/test-e2e/REQ-4.part1.spec.js"]
    assert mixed["kind"] == "mixed" and mixed["modified_leaves"] == ["REQ-4-1"], mixed
    assert [(t["leaf"], t["class"], t["line"]) for t in mixed["tests"]] == [("REQ-4-1", "unverified", 1), ("REQ-4-2", "valid", 2)]
    assert files["backend/test-e2e/REQ-9-1.spec.js"]["kind"] == "valid"
    lone = files["backend/test-e2e/helper-flow.spec.js"]
    assert not lone["mapped"] and lone["why"] and lone["tests"][0]["class"] == "unverified", lone
    assert reg["counts"] == {"files": 3, "tests": 4, "valid": 2, "unverified": 2, "superseded": 0, "unmapped_files": 1}, reg["counts"]
    assert ev.exempt_files(reg, ["REQ-4-1"]) == ["backend/test-e2e/REQ-4.part1.spec.js"] and ev.exempt_files(reg, ["REQ-4-2"]) == []
    assert ev.leaf_of_title(reg, "backend/test-e2e/REQ-4.part1.spec.js")["REQ-4-2: create a branch - Scenario 1"]["class"] == "valid"

@case
def a_file_of_several_inherited_leaves_without_ids_in_titles_is_valid():
    classes = {"classes": {"REQ-3-3": "inherited", "REQ-3-4": "inherited"}, "out_of_tree": []}
    handoff = {"leaves": [{"id": "REQ-3-3", "specs": ["REQ-3.part2.spec.js"]}, {"id": "REQ-3-4", "specs": ["REQ-3.part2.spec.js"]}]}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write(root, "backend/test-e2e/REQ-3.part2.spec.js", 'test("overview opens", async () => {});')
        reg = ev.register_specs(root, ["backend/test-e2e/REQ-3.part2.spec.js"], classes, handoff)
    entry = reg["files"]["backend/test-e2e/REQ-3.part2.spec.js"]
    assert entry["mapped"] and entry["tests"][0]["class"] == "valid" and entry["leaves"] == ["REQ-3-3", "REQ-3-4"], entry

@case
def effective_rules_keep_every_original_sentence_and_mark_overlaps():
    node = leaf("REQ-1-1-1", MODIFIED)
    yaml_text = NL.join(["- id: REQ-1-1-1", "  description: |-", *["    " + line for line in MODIFIED.split(NL)], "- id: REQ-1-1-2"])
    text, struct = ev.effective_rules(node, yaml_text)
    assert text.startswith("CURRENTLY EFFECTIVE RULES of REQ-1-1-1") and "scenarios decide" in text
    assert [m["kind"] for m in struct["modified"]] == ["keep", "change"], struct["modified"]
    first = struct["original"][0]
    assert first["overlaps"] == ["M2"] and first["state"] == "overlapped" and first["line"] == 5, first
    assert struct["original"][1]["overlaps"] == [] and "Username already exists" in text
    assert "where they differ, M2 applies" in text and "requirements.yaml line" in text
    assert ev.effective_rules(leaf("REQ-1"), "") == ("", {})
    json.dumps(struct)

@case
def spec_names_of_this_round():
    classes = {"modified": ["REQ-1-1-1"]}
    assert ev.batch_spec_prefix("REQ-1-1-1", ["REQ-1-1-1"], classes, {"REQ-1-1-1.spec.js"}) == "REQ-1-1-1.evo"
    assert ev.batch_spec_prefix("REQ-1-4", ["REQ-1-4"], classes, set()) == "REQ-1-4"
    assert ev.batch_spec_prefix("REQ-1.part1", ["REQ-1-4", "REQ-1-5"], classes, {"REQ-1.part1.spec.js"}) == "REQ-1.part1.evo"
    assert ev.batch_spec_prefix("REQ-1-1-1", ["REQ-1-1-1"], classes, {"REQ-1-1-1.evo.spec.js"}) == "REQ-1-1-1.evo2"
    from agent.continuation import spec_req_id
    assert spec_req_id("REQ-1-1-1.evo.spec.js") == "REQ-1-1-1"

@case
def scenario_seed_names_never_hold_a_password():
    node = leaf("REQ-1-1-1")
    node["scenarios"] = [{"name": "S3", "steps": [{"keyword": "GIVEN", "content":
        "The system has pre-provisioned the verified account `evo-existing` with email `evo@x.test` and password "
        "`Evo-Password-987!`; workbook `EVO-M01` holds `=C7+2` in D7."}]}]
    tree = {"id": "ROOT", "children": [node]}
    assert ev.scenario_seed_names(tree, ["REQ-1-1-1"]) == {"S3": ["evo-existing", "evo@x.test", "EVO-M01"]}

@case
def the_contract_lead_names_the_round_and_the_weak_spots():
    classes = {"modified": ["REQ-1"], "new": ["REQ-2"], "inherited": ["REQ-3"], "out_of_tree": ["REQ-9"],
               "earlier_status": {"REQ-3": "green", "REQ-9": "put back"}, "names": {"REQ-9": "Old feature"}}
    text = ev.contract_lead(classes, 4, {"debt": {"open_findings": [{"class": "Stated path", "where": "/x"}]}}, "EXISTING APPLICATION (x)")
    assert text.startswith("EXISTING APPLICATION (x):") and "modified (1): REQ-1" and "new (1): REQ-2" in text
    assert "1 of the original round outside it (REQ-9)" in text and "REQ-9 Old feature ended put back" in text
    assert "open finding: Stated path at /x" in text and "inherited means origin, not correctness" in text
    assert "edited only to add" not in text and "where its stated change needs it" in text

REAL = {
    "github": (Path("local-data/evolution/req/hackathon-evolution--github"), Path("local-data/analysis-1006/github-full/ws/template"), (42, 5, 5, 0)),
    "sheet": (Path("local-data/evolution/req/hackathon-evolution--sheet"), Path("local-data/analysis-1006/sheet/ws332/template"), (0, 5, 5, 19)),
}

@case
def the_real_evolution_round_classifies_as_measured():
    import hashlib

    import yaml

    present = [t for t, (req, app, _) in REAL.items() if (req / "requirements.yaml").is_file() and app.is_dir()]
    if not present:
        import unittest
        raise unittest.SkipTest("the evolution requirements / inherited deliverables are not on this machine")
    for task in present:
        req, app, want = REAL[task]
        raw = (req / "requirements.yaml").read_bytes()
        tree = yaml.safe_load(raw.decode("utf-8"))
        old, why = ev.inherited_requirement(app, hashlib.sha256(raw).hexdigest())
        handoff = json.loads((app / ".arc-handoff" / "handoff.json").read_text(encoding="utf-8"))
        specs = ev.inherited_spec_files(app)
        got = ev.classify(tree, ev.historical_inventory(tree, handoff, old, app, specs), old)
        counts = (len(got["inherited"]), len(got["modified"]), len(got["new"]), len(got["out_of_tree"]))
        assert counts == want and not got["unverified"] and not got["unmarked_changes"], (task, counts, got["unverified"])
        reg = ev.register_specs(app, specs, got, handoff)
        assert reg["counts"]["unmapped_files"] == 0 and reg["counts"]["files"] == len(specs), (task, reg["counts"])
        assert len(ev.to_implement(got)) == 10

def main() -> int:
    failed = 0
    skipped = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception as exc:
            import unittest
            if isinstance(exc, unittest.SkipTest):
                skipped += 1
                print(f"SKIP  {function.__name__}: {exc}")
                continue
            failed += 1
            traceback.print_exc()
            print(f"FAIL  {function.__name__}")
    print(f"{len(CASES) - failed - skipped}/{len(CASES)} v160a evolution cases passed" + (f"; {skipped} skipped" if skipped else ""))
    return 1 if failed else 0

if __name__ == "__main__":
    shutil.rmtree(Path(tempfile.gettempdir()) / "nonexistent-v160a", ignore_errors=True)
    raise SystemExit(main())
