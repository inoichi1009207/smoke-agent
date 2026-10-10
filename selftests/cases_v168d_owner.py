from __future__ import annotations

import inspect
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

def tree(leaves, root_description="A fixture application.", folder=None):
    import json

    def leaf_lines(leaf_id, description, steps, pad):
        out = [f"{pad}- id: {leaf_id}", f"{pad}  name: {leaf_id}", f"{pad}  type: ATOMIC",
               f"{pad}  description: {json.dumps(description, ensure_ascii=False)}"]
        if steps:
            out += [f"{pad}  scenarios:", f"{pad}  - name: {leaf_id} - Scenario 1", f"{pad}    steps:"]
            for keyword, content in steps:
                out += [f"{pad}    - keyword: {keyword}", f"{pad}      content: {json.dumps(content, ensure_ascii=False)}"]
        return out

    lines = ["id: ROOT", "name: Fixture", "type: FOLDER", f"description: {json.dumps(root_description)}", "children:"]
    grouped = set(folder[2]) if folder else set()
    if folder:
        lines += [f"- id: {folder[0]}", f"  name: {folder[0]}", "  type: FOLDER",
                  f"  description: {json.dumps(folder[1], ensure_ascii=False)}", "  children:"]
        for leaf in leaves:
            if leaf[0] in grouped:
                lines += leaf_lines(*leaf, pad="  ")
    for leaf in leaves:
        if leaf[0] not in grouped:
            lines += leaf_lines(*leaf, pad="")
    return "\n".join(lines) + "\n"

def project(tmp, files):
    root = Path(tmp)
    (root / "backend" / "src").mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root

WHOLE = tree([
    ("L-A", "The home page lists the workbooks.", [("THEN", "the page shows the workbook list")]),
    ("L-B", "The toolbar has an “Export CSV” button.", [("WHEN", "the user clicks the “Export CSV” button")]),
    ("L-C", "The sheet menu has a “Sort range” button.", []),
    ("L-D", "The editor has a “Sort range” button too.", []),
])

STAMPED = {"inherited_names_all": [], "inherited_names_complete": True}

EXPORT_LINK = 'export const Toolbar = () => <nav><a href="/export">Export CSV</a></nav>;\n'

@case
def a_name_one_leaf_states_is_owned_by_that_leaf_only():
    from agent import finding_owner as fo

    with Settings(**{KEY: True}):
        assert fo.name_owners(WHOLE, ["Export CSV"]) == ["L-B"]
        assert fo.name_owners(WHOLE, ["export   csv"]) == ["L-B"]

@case
def a_name_several_leaves_state_is_owned_by_all_of_them():
    from agent import finding_owner as fo

    with Settings(**{KEY: True}):
        assert fo.name_owners(WHOLE, ["Sort range"]) == ["L-C", "L-D"]

@case
def a_name_a_folder_or_root_states_goes_to_every_leaf_below_it():
    from agent import finding_owner as fo

    text = tree([("L-A", "Lists.", []), ("L-B", "The “Archive” button.", []), ("L-C", "Other.", [])],
                folder=("F-1", "Folder: every page has an “Archive” button.", ["L-A", "L-B"]))
    rooted = tree([("L-A", "Lists.", []), ("L-B", "The “Archive” button.", [])], root_description="The app has Archive.")
    with Settings(**{KEY: True}):
        assert fo.name_owners(text, ["Archive"]) == ["L-A", "L-B"]
        assert fo.name_owners(rooted, ["Archive"]) == ["L-A", "L-B"]

@case
def untraceable_names_have_no_owner():
    from agent import finding_owner as fo
    import cases_statedrole as sr

    with Settings(**{KEY: True}):
        assert fo.name_owners(WHOLE, ["Delete workbook"]) == []
        assert fo.name_owners(WHOLE, ["Export CSV"], inherited=["export csv"]) == []
        assert fo.name_owners("The toolbar has an “Export CSV” button.", ["Export CSV"]) == []
        assert fo.name_owners(sr._requirement_yaml([("REQ-1", "The “Export CSV” button.")]), ["Export CSV"]) == []
        assert fo.name_owners(WHOLE, []) == [] and fo.name_owners(WHOLE, ["  "]) == []
        assert fo.name_owners(WHOLE, ["Export CSV", "Delete workbook"]) == []
        assert fo.name_owners(WHOLE, ["Export"]) == ["L-B"]
        assert fo.name_owners(WHOLE, ["Expo"]) == []

@case
def a_source_name_needs_a_stated_sentence():
    from agent import finding_owner as fo

    text = tree([("L-A", "The picker has a “Role” select.", []), ("L-B", "Each row shows its role.", [])])
    with Settings(**{KEY: True}):
        assert fo.stated_name_owners(text, "Role") == ["L-A", "L-B"]
        assert fo.stated_name_owners(text, "Team") == []
        plain = tree([("L-A", "Each row shows its role.", [])])
        assert fo.stated_name_owners(plain, "role") == []

@case
def stated_role_findings_carry_the_stating_leaves():
    from agent import detaudit

    node = {"id": "L-A", "name": "L-A", "description": "The home page lists the workbooks.", "requirement_text": WHOLE, **STAMPED,
            "members": ["L-A"]}
    with tempfile.TemporaryDirectory() as tmp, Settings(**{KEY: True}):
        root = project(tmp, {"frontend/src/components/Toolbar.tsx": EXPORT_LINK})
        lookup = detaudit.owner_lookup(node)
        found, counts = detaudit.stated_control_findings(node, root, owner_of=lookup[0])
        plain, _ = detaudit.stated_control_findings(node, root)
    mine = [f for f in found if "Export CSV" in f["problem"]]
    assert counts["stated_role"] == 1 and len(mine) == 1, found
    assert mine[0]["owner_leaf"] == ["L-B"], mine
    assert [{k: v for k, v in f.items() if k != "owner_leaf"} for f in found] == plain
    assert all("owner_leaf" not in f for f in plain)

@case
def stated_missing_findings_carry_the_batchs_own_leaf():
    from agent import detaudit

    node = {"id": "L-C", "name": "L-C", "description": "The sheet menu has a “Sort range” button.", "requirement_text": WHOLE, **STAMPED,
            "members": ["L-C"]}
    with tempfile.TemporaryDirectory() as tmp, Settings(**{KEY: True}):
        root = project(tmp, {"frontend/src/components/Toolbar.tsx": EXPORT_LINK})
        found, counts = detaudit.stated_control_findings(node, root, owner_of=detaudit.owner_lookup(node)[0])
    missing = [f for f in found if "Sort range" in f["problem"]]
    assert counts["stated_missing"] == 1 and missing[0]["owner_leaf"] == ["L-C", "L-D"], found

@case
def nav_edge_findings_carry_the_leaves_naming_the_control():
    from agent import detaudit

    items = [{"req": "L-B", "verdict": "WRONG_ROLE", "confidence": "high", "quote": "the “Export CSV” button opens the export",
              "source": "the toolbar", "source_resolved": "/", "expected": "button named 'Export CSV'", "have": ["link"]},
             {"req": "?", "verdict": "NOT_A_CONTROL", "confidence": "high", "quote": "q", "source": "s",
              "source_resolved": "/", "expected": "link named 'Sort range'", "have": ["h1"]},
             {"req": "L-A", "verdict": "NOT_A_CONTROL", "confidence": "high", "quote": "q", "source": "s",
              "source_resolved": "/", "expected": "link named 'Sort range'", "have": ["h1"]}]
    original = detaudit.missing_stated_edges
    detaudit.missing_stated_edges = lambda whole, project_dir: [dict(i) for i in items]
    try:
        node = {"id": "L-A", "requirement_text": WHOLE, **STAMPED, "members": ["L-A"]}
        with Settings(**{KEY: True}):
            found, _logged = detaudit.nav_edge_findings(WHOLE, Path("."), [], owner_of=detaudit.owner_lookup(node)[0])
            plain, _ = detaudit.nav_edge_findings(WHOLE, Path("."), [])
    finally:
        detaudit.missing_stated_edges = original
    assert [f.get("owner_leaf") for f in found] == [["L-B"], None, None], found
    assert all("owner_leaf" not in f for f in plain)

@case
def duplicate_names_carry_owners_only_for_stated_names():
    from agent.duplicate_names import duplicate_name_source_findings
    from agent import detaudit

    page = ("export function Access() {\n  return (<form>\n    <select aria-label=\"Role\"><option>Read</option></select>\n"
            "    <select aria-label=\"Role\"><option>Write</option></select>\n"
            "    <input aria-label=\"Team\" />\n    <input aria-label=\"Team\" />\n  </form>);\n}\n")
    whole = tree([("L-A", "The home page.", []), ("L-B", "The access page has a “Role” select per grant.", [])])
    node = {"id": "L-A", "requirement_text": whole, **STAMPED, "members": ["L-A"]}
    with tempfile.TemporaryDirectory() as tmp, Settings(**{KEY: True}):
        root = project(tmp, {"frontend/src/pages/Access.tsx": page})
        found = duplicate_name_source_findings(root, log=lambda *_: None, owner_of=detaudit.owner_lookup(node)[1])
        plain = duplicate_name_source_findings(root, log=lambda *_: None)
    by_name = {f["requirement"]: f for f in found}
    assert set(by_name) == {"Role", "Team"}, found
    assert by_name["Role"]["owner_leaf"] == ["L-B"] and "owner_leaf" not in by_name["Team"], found
    assert all("owner_leaf" not in f for f in plain) and len(plain) == 2

@case
def untraceable_kinds_stay_untagged_in_the_whole_layer():
    from agent import detaudit

    node = {"id": "L-A", "name": "L-A", "description": "The home page lists the workbooks.", "requirement_text": WHOLE, **STAMPED,
            "members": ["L-A"]}
    snapshot = [{"route": "/", "aria": "- list:\n  - listitem:\n    - link \"Q3 Sales\":\n      - /url: /w/1\n  - listitem:\n"
                 "    - link \"Budget\":\n      - /url: /w/2\n", "text": "", "console": ["Failed to load resource: 404"]}]
    with tempfile.TemporaryDirectory() as tmp, Settings(**{KEY: True, "det_outsider_walk": False, "det_session_probe": False,
                                                            "det_plan_walk": False}):
        root = project(tmp, {"frontend/src/components/Toolbar.tsx": EXPORT_LINK})
        found = detaudit.deterministic_findings(root, node, snapshot, log=lambda *_: None)
    tagged = {f["where"]: f.get("owner_leaf") for f in found}
    assert tagged.get("frontend/src/components/Toolbar.tsx:1") == ["L-B"], found
    console = [f for f in found if "console" in f["problem"]]
    assert console and all("owner_leaf" not in f for f in console), found
    assert all("owner_leaf" not in f for f in found if "through a LINK" in f["problem"]), found

@case
def switch_off_tags_nothing_and_builds_no_lookup():
    from agent import detaudit
    from agent import finding_owner as fo

    node = {"id": "L-A", "name": "L-A", "description": "The home page lists the workbooks.", "requirement_text": WHOLE, **STAMPED,
            "members": ["L-A"]}
    with Settings(**{KEY: False}):
        assert detaudit.owner_lookup(node) == (None, None)
        assert fo.name_owners(WHOLE, ["Export CSV"]) == [] and fo.stated_name_owners(WHOLE, "Export CSV") == []
        with tempfile.TemporaryDirectory() as tmp:
            root = project(tmp, {"frontend/src/components/Toolbar.tsx": EXPORT_LINK})
            found = detaudit.deterministic_findings(root, node, [], log=lambda *_: None)
    assert found and all("owner_leaf" not in f for f in found), found

@case
def the_layer_wires_the_lookups_and_new_parameters_default_to_none():
    from agent import detaudit, duplicate_names

    body = inspect.getsource(detaudit.deterministic_findings)
    assert "owner_lookup(node)" in body
    assert "stated_control_findings(node, project_dir, owner_of=" in body
    assert "nav_edge_findings(whole, project_dir, findings, owner_of=" in body
    assert "source_owner_of=" in body
    for function, name in ((detaudit.stated_control_findings, "owner_of"), (detaudit.nav_edge_findings, "owner_of"),
                           (detaudit.v149_findings, "source_owner_of"),
                           (duplicate_names.duplicate_name_source_findings, "owner_of")):
        assert inspect.signature(function).parameters[name].default is None, function

@case
def without_the_complete_inherited_set_nothing_is_tagged():
    from agent import detaudit

    base = {"id": "L-A", "requirement_text": WHOLE, "members": ["L-A"]}
    with Settings(**{KEY: True}):
        assert detaudit.owner_lookup(dict(base)) == (None, None)
        assert detaudit.owner_lookup(dict(base, inherited_names=["x"])) == (None, None)
        assert detaudit.owner_lookup(dict(base, inherited_names_all=[], inherited_names_complete=False)) == (None, None)
        owner_of = detaudit.owner_lookup(dict(base, **STAMPED))[0]
        assert owner_of is not None and owner_of("Export CSV") == ["L-B"]

@case
def an_inherited_name_past_the_old_cut_of_400_still_blocks_ownership():
    from agent import detaudit

    names = [f"Aaa {i:04d}" for i in range(450)] + ["Export CSV"]
    node = {"id": "L-A", "requirement_text": WHOLE, "members": ["L-A"], "inherited_names": sorted(names)[:400],
            "inherited_names_all": sorted(names), "inherited_names_complete": True}
    assert "Export CSV" not in node["inherited_names"]
    with Settings(**{KEY: True}):
        assert detaudit.owner_lookup(node)[0]("Export CSV") == []

@case
def stamping_writes_the_whole_set_only_with_the_switch_on():
    import main as m
    from agent import evolution

    leaves = {f"OLD-{i}": {"id": f"OLD-{i}", "name": f"OLD-{i}", "description": f"The page has a “Control {i:04d}” button."}
              for i in range(450)}
    state = {"active": True, "classes": {"classes": {leaf: "inherited" for leaf in leaves}}, "implement": []}
    original = evolution.tree_index
    saved_log = m.log
    try:
        evolution.tree_index = lambda tree: (dict(leaves), {}, {})
        m.log = lambda *_: None
        for on in (True, False):
            f = m.Factory.__new__(m.Factory)
            f.evolution = state
            work = [{"id": "B1", "members": ["NEW-1"]}]
            with Settings(**{KEY: on}):
                f.stamp_inherited_names(work, {"id": "ROOT"})
            assert len(work[0]["inherited_names"]) == m.Factory.INHERITED_NAMES_MAX
            if on:
                assert len(work[0]["inherited_names_all"]) == 450 and work[0]["inherited_names_complete"] is True
            else:
                assert "inherited_names_all" not in work[0] and "inherited_names_complete" not in work[0]
    finally:
        evolution.tree_index = original
        m.log = saved_log

@case
def the_real_sheet_requirement_owns_edit_name_by_req_7_1_1_only():
    path = ROOT.parent / "evolution" / "req" / "hackathon-evolution--sheet" / "requirements.yaml"
    if not path.is_file():
        print("      (skipped: the sheet requirement is not on this machine)")
        return
    from agent import finding_owner as fo

    with Settings(**{KEY: True}):
        assert fo.name_owners(path.read_text(encoding="utf-8"), ["Edit <name>"]) == ["REQ-7-1-1"]

@case
def the_real_github_requirement_sends_the_role_duplicate_now():
    path = ROOT.parent / "evolution" / "req" / "hackathon-evolution--github" / "requirements.yaml"
    if not path.is_file():
        print("      (skipped: the github requirement is not on this machine)")
        return
    from agent import finding_owner as fo

    with Settings(**{KEY: True}):
        owners = fo.stated_name_owners(path.read_text(encoding="utf-8"), "Role")
    assert "REQ-1-1-3" in owners and len(owners) > 40, owners

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
    print(f"{len(CASES) - failed}/{len(CASES)} v168d owner cases passed in {time.time() - started:.1f}s")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
