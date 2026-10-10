from __future__ import annotations

import copy
import os
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "selftests"))

import cases_v144g as g

CASES = []
QUIET = lambda message: None

def case(function):
    CASES.append(function)
    return function

IDLE_ARROW = "Arrow keys move the cell selection when no edit is in progress."

def said(text):
    from agent.commit_probe import key_statements
    return key_statements(text)

def findings(statement, result, logs=None):
    from agent.commit_probe import keys_findings
    return keys_findings(statement, result, log=(logs.append if logs is not None else QUIET))

def kinds(found):
    return sorted("arrow-idle" if f["problem"].startswith("ArrowDown did not move the selection: the probe clicked")
                  else g.kinds([f])[0] for f in found)

def with_keys(base, **parts):
    result = copy.deepcopy(base)
    for name, value in parts.items():
        result["keys"][name] = value
    return result

@case
def key_statements_keep_the_mode_and_drop_clause_negations():
    for text, name, mode in ((IDLE_ARROW, "arrow_moves", "idle"),
                             ("When no cell is being edited, arrow keys move the selection between cells.", "arrow_moves", "idle"),
                             ("When not editing, the arrow keys move the active cell.", "arrow_moves", "idle"),
                             ("Outside edit mode, arrow keys move the selection to the adjacent cell.", "arrow_moves", "idle"),
                             ("While editing a cell, arrow keys move to the next cell.", "arrow_moves", "editing"),
                             ("While a cell is being edited, pressing Enter commits the change.", "enter_commits", "editing"),
                             ("When no edit is in progress, typing on a selected cell replaces its content.", "replace", "idle"),
                             ("Enter moves the selection down to the cell below when no edit is in progress.", "enter_moves", "idle")):
        got = said(text)
        assert got and name in got["said"] and got.get("modes", {}).get(name) == mode, (text, got)

    assert said(g.SHEET) == {"thing": "cell", "roles": ["gridcell", "cell"],
                             "said": {"enter_commits": "Pressing Enter or clicking another cell commits the change;"}}
    plain = said("Arrow keys move the selection between cells.")
    assert plain and "modes" not in plain and "arrow_moves" in plain["said"], plain

    own = said("When typing on a selected cell, the typed text replaces its content.")
    assert own and "replace" in own["said"] and "modes" not in own, own
    assert said("While editing, typing replaces the selected text of the cell.")["modes"] == {"replace": "editing"}

    for text in ("Arrow keys move the caret, not the cell selection.",
                 "Arrow keys move the caret while editing a cell.",
                 "While editing, arrow keys never move the selection to another cell.",
                 "Typing on a selected cell replaces its content only after a double-click, never directly."):
        got = said(text)
        assert got is None, (text, got)

    got = said("Pressing Enter commits the cell value; the grid does not reload.")
    assert got and "enter_commits" in got["said"], got

TYPED_ARROW_CARET = {"mode": "type", "key": "ArrowDown", "cell": "H1", "below": "H2", "shown": True, "movedDown": False,
                     "stayed": True, "shownAfter": True, "shownSettled": True, "stored": False}

@case
def the_audit_input_is_quiet_and_the_idle_arrow_is_tested_idle():
    from agent.commit_probe import keys_job
    stated = said(IDLE_ARROW)

    caret = with_keys(g.LOCAL, arrow=TYPED_ARROW_CARET)
    logs = []
    assert findings(stated, caret, logs) == [], findings(stated, caret)

    stuck = with_keys(caret, arrowIdle={"mode": "idle", "key": "ArrowDown", "cell": "C4", "below": "C5", "selected": True,
                                        "editing": False, "movedDown": False, "stayed": True, "editingAfter": False})
    found = findings(stated, stuck)
    assert kinds(found) == ["arrow-idle"], kinds(found)
    assert "no edit open" in found[0]["fix"] and "Tab" not in found[0]["fix"] and "Enter" not in found[0]["fix"], found

    for change in ({"movedDown": True, "stayed": False}, {"editing": True, "movedDown": None, "stayed": None},
                   {"selected": False}, {"stayed": False}):
        other = copy.deepcopy(stuck)
        other["keys"]["arrowIdle"].update(change)
        assert findings(stated, other) == [], (change, findings(stated, other))

    assert keys_job(stated, None)["idle"] == ["arrow_moves"]
    assert keys_job(said("Arrow keys move the selection between cells."), None)["idle"] == ["arrow_moves"]
    assert "idle" not in keys_job(said("While editing a cell, arrow keys move to the next cell."), None)
    assert "idle" not in keys_job(said(g.SHEET), None)

@case
def an_unconditioned_arrow_statement_is_not_judged_on_the_typing_step():
    plain = said("Arrow keys move the selection between cells.")
    caret = with_keys(g.LOCAL, arrow=TYPED_ARROW_CARET)
    assert findings(plain, caret) == [], findings(plain, caret)

    editing = said("While editing a cell, arrow keys move to the next cell.")
    found = findings(editing, caret)
    assert [f["problem"][:45] for f in found] == ["ArrowDown did not move the selection while ed"], found
    idle_stuck = with_keys(g.LOCAL, arrow={}, arrowIdle={"selected": True, "editing": False, "movedDown": False, "stayed": True})
    assert findings(editing, idle_stuck) == [], findings(editing, idle_stuck)

@case
def enter_tab_and_replace_stated_for_another_mode_are_logged_only():
    for text in ("Enter moves the selection down to the cell below when no edit is in progress.",
                 "When no edit is in progress, Tab moves the selection to the next cell on the right.",
                 "While editing, typing replaces the selected text of the cell."):
        stated = said(text)
        assert stated and stated.get("modes"), (text, stated)
        logs = []
        assert findings(stated, g.LOCAL, logs) == [], (text, findings(stated, g.LOCAL))
        assert any("stated only in another mode" in line for line in logs), logs

    for text, kind in (("Enter moves down to the cell below.", "enter-moves"),
                       ("While editing, Enter moves down to the cell below.", "enter-moves"),
                       ("Pressing Tab moves to the next cell on the right.", "tab-moves"),
                       ("Selecting a cell and typing replaces its content.", "replace"),
                       ("When no edit is in progress, typing on a selected cell replaces its content.", "replace")):
        assert kinds(findings(said(text), g.LOCAL)) == [kind], (text, kinds(findings(said(text), g.LOCAL)))

@case
def each_fix_names_only_its_own_behaviour():
    from agent import commit_probe as cp
    found = findings(said(g.SHEET), g.PLATFORM)
    assert g.kinds(found) == ["arrow", "tab-merged"], g.kinds(found)
    for f in found:
        assert "contentEditable" not in f["fix"] and "keydown" not in f["fix"], f["fix"]
    tab = next(f for f in found if f["problem"].startswith("Tab merged"))
    arrow = next(f for f in found if f["problem"].startswith("An arrow key"))
    assert "Tab" in tab["fix"] and "arrow" not in tab["fix"].lower() and "moves the selection" not in tab["fix"], tab["fix"]
    assert "arrow key" in arrow["fix"] and "Tab" not in arrow["fix"] and "REPLACES" not in arrow["fix"], arrow["fix"]

    stated = said(g.SHEET + " Pressing Tab moves to the next cell on the right.")
    tab2 = next(f for f in findings(stated, g.PLATFORM) if f["problem"].startswith("Tab merged"))
    assert "next cell on the right" in tab2["fix"], tab2["fix"]

    everything = said(g.SHEET + " Selecting a cell and typing replaces its content. Enter moves down to the cell below.")
    for f in findings(everything, g.PLATFORM):
        other = {"enter-moves": ("Tab", "arrow"), "replace": ("Tab", "arrow", "Enter"), "tab-merged": ("arrow", "Enter moves"),
                 "arrow": ("Tab",)}[g.kinds([f])[0]]
        assert not any(word in f["fix"] for word in other), (g.kinds([f]), f["fix"])
    assert not hasattr(cp, "_KEYS_FIX")

@case
def the_js_idle_step_is_gated_and_types_nothing():
    from agent.commit_probe import PROBE_SCRIPT as s
    start = s.index("const ti = (job.idle || []).includes('arrow_moves')")
    end = s.index("// typing over a filled instance")
    step = s[start:end]
    assert "if (r.selected && !r.editing && st0.inGrid)" in step, step
    assert "keyboard.type" not in step and "edit(" not in step, step
    assert "out.keys.arrowIdle = r;" in step

COMMANDS = ('The row-number menu provides "Insert 1 row above" and "Delete row". The column-header menu provides '
            '"Insert 1 column left" and "Delete column". ')

def structure(text):
    from agent.structure_probe import structure_statement
    return structure_statement(text)

@case
def a_negated_or_conditioned_shift_sentence_switches_nothing_on():

    assert structure(COMMANDS + "Formula references do not shift when a row is inserted.") is None
    assert structure(COMMANDS + "Formula references are never adjusted when rows or columns are inserted.") is None
    assert structure(COMMANDS + 'When the "Adjust references" option is enabled, formula references shift on insertion.') is None
    assert structure(COMMANDS + "Formula references shift on insertion unless the sheet is protected.") is None

    for text in ("If a row is inserted above a referenced cell, the formula references shift down.",
                 "When a row is inserted, formula references shift downward together.",
                 "On insertion, the formula references shift downward together."):
        got = structure(COMMANDS + text)
        assert got and got["axis"] == "row" and got["shift_sentence"] == text, (text, got)

    assert structure(COMMANDS + "Formula references adjust when a row is deleted.") is None

    got = structure(COMMANDS + "On insertion, formula references shift together. Formula references do not shift when "
                               "a row is inserted.")
    assert got and got["axis"] == "column" and got["other_axis"] is None, got
    got = structure(COMMANDS + "On insertion, formula references shift together. Formula references do not shift when "
                               "a column is inserted.")
    assert got and got["axis"] == "row" and got["other_axis"] is None, got

    got = structure(COMMANDS + "On insertion, the formula references shift downward together, and no data is lost.")
    assert got and got["axis"] == "row" and got["other_axis"]["axis"] == "column", got

@case
def the_sheet_statement_is_unchanged():
    path = Path("local-data/try/sheet-v141/template/requirements/requirements.yaml")
    if not path.is_file():
        print("  skipped: the sheet requirement is not present")
        return
    got = structure(path.read_text(encoding="utf-8"))
    assert got["axis"] == "row" and got["shift_sentence"].startswith("On insertion, the target row"), got
    assert got["other_axis"] == {"axis": "column", "header": "A", "insert": "Insert 1 column left",
                                 "delete": "Delete column"}, got["other_axis"]
    assert got["sort"] and got["pivot"] and got["absolute"] and got["lost_token"] == "#REF!", got

def _fixture(mode: str) -> str:
    server = g.FIXTURE_SERVER.replace("const MODE = process.env.FIXTURE_MODE === 'correct' ? 'correct' : 'broken';",
                                      "const MODE = process.env.FIXTURE_MODE || 'broken';")
    server = server.replace("if (ev.key === 'ArrowDown') {", "if (ev.key === 'ArrowDown' && !(MODE === 'stuck' && !edit)) {")
    assert server.count("MODE === 'stuck'") == 1 and "FIXTURE_MODE || 'broken'" in server
    return server

def browser_fixture(mode: str, backend: Path, statement) -> list[dict[str, str]]:
    from agent.commit_probe import keys_job, run_probe
    with tempfile.TemporaryDirectory() as tmp:
        server_js = Path(tmp) / "fixture.cjs"
        server_js.write_text(_fixture(mode), encoding="utf-8")
        port = g._free_port()
        server = subprocess.Popen(["node", str(server_js)], env={**os.environ, "PORT": str(port), "FIXTURE_MODE": mode},
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)
                    break
                except Exception:
                    time.sleep(0.2)
            result = run_probe(backend, f"http://127.0.0.1:{port}", keys_job(statement, None), log=print)
            print(f"  {mode}: arrowIdle={result.get('keys', {}).get('arrowIdle') if isinstance(result, dict) else result}")
            assert isinstance(result, dict) and not result.get("error") and isinstance(result.get("keys"), dict), result
            return findings(statement, result)
        finally:
            server.kill()
            server.wait(timeout=10)

def browser_main() -> int:
    backend = Path(os.environ.get("ARC_PLAYWRIGHT_BACKEND") or ROOT / "template" / "backend")
    if not (backend / "node_modules" / "@playwright" / "test").is_dir():
        print(f"skipped  browser fixtures: no node_modules/@playwright/test under {backend.name} (set ARC_PLAYWRIGHT_BACKEND)")
        return 0
    failed = 0
    stated = said(g.SHEET + " " + IDLE_ARROW)
    for mode, want in (("stuck", ["arrow-idle"]), ("correct", [])):
        try:
            got = kinds(browser_fixture(mode, backend, stated))
            assert got == want, (mode, got, want)
            print(f"ok    browser fixture {mode}: {got}")
        except Exception:
            failed += 1
            print(f"FAIL  browser fixture {mode}")
            traceback.print_exc(limit=4)
    return failed

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
    print(f"{len(CASES) - failed}/{len(CASES)} r49w cases passed")
    if "--browser" in sys.argv:
        failed += browser_main()
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
