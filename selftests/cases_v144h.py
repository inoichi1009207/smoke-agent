from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES = []
QUIET = lambda message: None

def case(function):
    CASES.append(function)
    return function

CREATE_DESC = ('Users create a blank workbook from the workbook home page. The home page provides a button with the '
               'accessible name "New blank workbook"; clicking it opens the creation page, whose submit button is named '
               '"Create". After creation succeeds, the editor opens and shows only a blank worksheet named Sheet1, with '
               'Sheet1 active and A1 selected; refreshing or returning to the home page and reopening produces the same '
               'state.')
ROWS_DESC = ('Users insert blank rows above or below a target row, or delete the target row, through the row-number menu in '
             'the current active worksheet. The row-number menu provides "Insert 1 row above", "Insert 1 row below", and '
             '"Delete row". On insertion, the target row and all subsequent complete records, validation rules, and formula '
             'references shift downward together; on deletion, subsequent rows shift upward and rules on the target row '
             'are removed. Affected formulas display the adjusted original formulas and correct results, and references '
             'that cannot be preserved display an explicit error; filters continue to apply to the original data region.')
COLS_DESC = ('Users insert a blank column to the left or right of a target column, or delete the target column, through the '
             'column-header menu in the current active worksheet. The column-header menu provides "Insert 1 column left", '
             '"Insert 1 column right", and "Delete column". On insertion, all complete data, validation rules, and formula '
             'references in the target column and subsequent columns shift right together; on deletion, subsequent '
             'columns shift left and rules on the target column are removed. Data outside the deleted column is '
             'preserved; affected formulas display the adjusted original formulas and correct results, while direct '
             'references that cannot be preserved display #REF!; filters continue to apply to the adjusted region.')
FORMULA_DESC = ('Supports basic formula calculation, relative and absolute references, dependency recalculation, and error '
                'handling within the current active worksheet. The grid displays formula results or errors, while the '
                'formula bar displays the expression submitted by the user; copy, paste, row/column changes, and '
                'source-value edits follow the same reference-adjustment and recalculation rules.')
BAR_DESC = ('Supports entering basic expressions and aggregate functions through the grid and "Formula bar" from REQ-3-1-1 '
            'and copying formulas through REQ-3-2-1.')
ERRORS_DESC = ('Formula errors use stable visible values: division by zero displays #DIV/0!, an invalid reference displays '
               '#REF!, an unsupported function displays #NAME?, a malformed expression displays #ERROR!, and a direct or '
               'indirect circular reference displays #REF!. When an error cell is selected, the formula bar displays the '
               'original formula submitted by the user; after refresh, both the error value and original formula persist.')

def yaml_like(*parts: tuple[str, str]) -> str:
    return "".join(f"- id: {rid}\n  description: |\n    {desc}\n" for rid, desc in parts)

SHEET = yaml_like(("REQ-1-2-1", CREATE_DESC), ("REQ-2-2-1", ROWS_DESC), ("REQ-2-2-2", COLS_DESC), ("REQ-4", FORMULA_DESC),
                  ("REQ-4-1", BAR_DESC), ("REQ-4-2-2", ERRORS_DESC))

P141 = json.loads(r'''{
 "axis": "row",
 "route": "/workbooks/2",
 "created": "New blank workbook",
 "bar": true,
 "setup": {
  "A1": {
   "text": "2",
   "formula": "2"
  },
  "A2": {
   "text": "20",
   "formula": "=A1*10"
  },
  "A3": {
   "text": "3",
   "formula": "=$A$1+1"
  }
 },
 "insert": {
  "how": "right-click"
 },
 "afterInsert": {
  "old": {
   "text": ""
  },
  "A2": {
   "text": "2",
   "formula": "2"
  },
  "A3": {
   "text": "20",
   "formula": "=A2*10"
  },
  "A4": {
   "text": "3",
   "formula": "=A2+1"
  }
 },
 "del": {
  "how": "right-click"
 },
 "afterDelete": {
  "A2": {
   "text": "#REF!",
   "formula": "=#REF!"
  },
  "A3": {
   "text": "#REF!",
   "formula": "=#REF!"
  }
 },
 "samples": [
  {
   "cell": "F1",
   "formula": "=1/0",
   "before": "",
   "text": "#DIV/0!"
  },
  {
   "cell": "F2",
   "formula": "=NOSUCHFUNC(1)",
   "before": "",
   "text": "#NAME?"
  },
  {
   "cell": "F3",
   "formula": "=(1+2",
   "before": "",
   "text": "#ERROR!"
  },
  {
   "cell": "F4",
   "formula": "=SUM(E1:E2",
   "before": "",
   "text": "#ERROR!"
  },
  {
   "cell": "F5",
   "formula": "=SUM(F5:F6)",
   "before": "",
   "text": "#REF!"
  }
 ],
 "visited": [
  "/"
 ],
 "ms": 20282
}''')

P141["afterDelete"]["old"] = {"text": ""}

SHIFT = "References not shifted on insert"
DOLLAR = "`$` markers removed by a"
DELETED = "Reference to a deleted"
ERRORS = "Formula error values differ"
DIAG = "(diagnostic, not reported)"

def kinds(found, prefix):
    return [f for f in found if f["problem"].startswith(prefix)]

def statement():
    from agent.structure_probe import structure_statement
    said = structure_statement(SHEET)
    assert said, said
    return said

def job(said=None):
    from agent.structure_probe import probe_job
    return probe_job(said or statement(), None)

def replay(**parts):
    result = json.loads(json.dumps(P141))
    for key, value in parts.items():
        if "." in key:
            part, coord = key.split(".", 1)
            result[part][coord] = value
        else:
            result[key] = value
    return result

def findings(result, said=None, logs=None):
    from agent.structure_probe import structure_findings
    said = said or statement()
    return structure_findings(said, job(said), result, log=(logs.append if logs is not None else QUIET))

def diag_logged(logs, words):
    return any(DIAG in line and words in line for line in logs)

@case
def the_sheet_statement_is_recognised_with_its_commands_and_error_values():
    said = statement()
    assert said["axis"] == "row" and said["header"] == "1", said
    assert said["insert"] == "Insert 1 row above" and said["delete"] == "Delete row", said
    assert said["absolute"] is True and "absolute references" in said["absolute_sentence"], said
    assert "shift" in said["shift_sentence"] and "formula references" in said["shift_sentence"], said
    assert said["lost_token"] == "#REF!" and "cannot be preserved" in said["lost_sentence"], said
    tokens = {e["kind"]: e["token"] for e in said["errors"]}
    assert tokens == {"division by zero": "#DIV/0!", "unsupported function": "#NAME?", "malformed expression": "#ERROR!",
                      "circular reference": "#REF!"}, tokens
    assert said["formula_bar"] == "Formula bar", said
    assert said["create"][0] == "New blank workbook" and said["submit"] == "Create", said

@case
def no_statement_without_quoted_commands_or_the_shift_sentence():
    from agent.structure_probe import probe_job, structure_statement
    unquoted = ROWS_DESC.replace('"', "")
    assert structure_statement(yaml_like(("REQ-2-2-1", unquoted), ("REQ-4-2-2", ERRORS_DESC))) is None
    commands_only = ROWS_DESC.split(" On insertion")[0]
    assert '"Insert 1 row above"' in commands_only
    assert structure_statement(yaml_like(("REQ-2-2-1", commands_only), ("REQ-4-2-2", ERRORS_DESC))) is None
    sheets_only = ('The tab bar provides "Add worksheet" and "Delete worksheet". On insertion, all formula references '
                   'shift downward together.')
    assert structure_statement(yaml_like(("REQ-1", sheets_only))) is None
    assert structure_statement("") is None and structure_statement(None) is None
    assert probe_job(None, []) is None

@case
def github_like_requirements_give_no_statement():
    base = Path("local-data/local-eval/research/heldout-probes/apps")
    from agent.structure_probe import probe_job, structure_statement
    seen = 0
    for name in ("v141-github-2", "v138-github-1"):
        path = base / name / "requirements" / "requirements.yaml"
        if not path.is_file():
            print(f"  skipped {name}: requirement not present")
            continue
        whole = path.read_text(encoding="utf-8")
        said = structure_statement(whole)
        assert said is None, (name, said)
        assert probe_job(said, []) is None
        seen += 1
    print(f"  github-like requirements checked: {seen}")

@case
def the_stated_side_is_taken_whatever_the_order():
    from agent.structure_probe import structure_statement
    shift = " On insertion, the formula references shift downward together."
    rows = 'The row-number menu provides "Insert 1 row below", "Insert 1 row above", and "Delete row".' + shift
    assert structure_statement(yaml_like(("R", rows)))["insert"] == "Insert 1 row above"
    below_only = 'The row-number menu provides "Insert 1 row below" and "Delete row".' + shift
    assert structure_statement(yaml_like(("R", below_only))) is None
    cols = ('The column-header menu provides "Insert 1 column right", "Insert 1 column left", and "Delete column".'
            " On insertion, the formula references shift right together.")
    said = structure_statement(yaml_like(("R", cols)))
    assert said["axis"] == "column" and said["insert"] == "Insert 1 column left", said

@case
def a_columns_only_requirement_plans_along_row_1():
    from agent.structure_probe import probe_job, structure_statement
    said = structure_statement(yaml_like(("REQ-2-2-2", COLS_DESC)))
    assert said["axis"] == "column" and said["header"] == "A", said
    assert said["insert"] == "Insert 1 column left" and said["delete"] == "Delete column", said
    assert said["errors"] == [] and said["formula_bar"] == "" and said["create"] == [], said
    plan = probe_job(said, None)["plan"]
    assert (plan["value"], plan["rel"], plan["abs"]) == ("A1", "B1", "C1"), plan
    assert (plan["i_value"], plan["i_rel"], plan["i_abs"]) == ("B1", "C1", "D1"), plan
    assert plan["rel_formula"] == "=A1*10" and plan["abs_formula"] == "=$A$1+1", plan
    assert plan["i_rel_expect"] == "=B1*10" and plan["i_abs_expect"] == "=$B$1+1", plan
    assert plan["delete_header"] == "B" and plan["d_rel"] == "B1", plan

@case
def the_row_job_plans_cells_samples_and_grid_routes():
    from agent.structure_probe import probe_job
    said = statement()
    grid = '- grid "Sheet":\n  - row "1":\n    - gridcell "A1"'
    snapshot = [{"route": "/", "aria": '- button "New item"'},
                {"route": "/items/2", "aria": grid},
                {"route": "/items/:id", "aria": grid},
                {"route": "/broken", "aria": grid, "error": "500"},
                {"route": "/board", "aria": "", "hasGrid": True},
                {"route": "nothing", "aria": grid}]
    planned = probe_job(said, snapshot)
    assert planned["routes"] == ["/items/2", "/board"], planned["routes"]
    assert probe_job(said, None)["routes"] == []
    assert planned["axis"] == "row" and planned["header"] == "1" and planned["bar"] == "Formula bar", planned
    assert planned["insert"] == "Insert 1 row above" and planned["delete"] == "Delete row", planned
    assert planned["create"][0] == "New blank workbook" and planned["submit"] == "Create", planned
    plan = planned["plan"]
    assert (plan["value"], plan["rel"], plan["abs"]) == ("A1", "A2", "A3"), plan
    assert (plan["i_value"], plan["i_rel"], plan["i_abs"]) == ("A2", "A3", "A4"), plan
    assert plan["i_rel_expect"] == "=A2*10" and plan["i_abs_expect"] == "=$A$2+1", plan
    assert plan["delete_header"] == "2" and plan["d_rel"] == "A2", plan
    samples = planned["samples"]
    assert all(s["cell"].startswith("F") for s in samples), samples
    assert len({s["cell"] for s in samples}) == len(samples) == 5, samples
    circular = [s for s in samples if s["kind"] == "circular reference"]
    assert len(circular) == 1 and circular[0]["cell"] in circular[0]["formula"], circular
    assert "{" not in "".join(s["formula"] for s in samples), samples
    for s in samples:
        assert s["cell"] in planned["used"]
    assert {"A1", "A2", "A3", "A4"} <= set(planned["used"]), planned["used"]

@case
def the_p141_replay_reports_only_the_dropped_dollars():
    logs = []
    found = findings(P141, logs=logs)
    assert len(found) == 1 and kinds(found, DOLLAR), found
    problem = found[0]["problem"]
    assert "=A2+1" in problem and "=$A$2+1" in problem, problem
    assert found[0]["where"] == f"route {P141['route']}" and found[0]["fix"] and found[0]["requirement"], found
    assert not any(DIAG in line for line in logs), logs

@case
def an_unshifted_relative_reference_is_reported():
    found = findings(replay(**{"afterInsert.A3": {"text": "0", "formula": "=A1*10"}}))
    shifted = kinds(found, SHIFT)
    assert len(shifted) == 1, found
    assert "=A1*10" in shifted[0]["problem"] and "=A2*10" in shifted[0]["problem"], shifted
    assert not kinds(found, DELETED), found

@case
def a_reference_re_pointed_after_delete_is_reported():
    found = findings(replay(**{"afterDelete.A2": {"text": "0", "formula": "=A2*10"}}))
    deleted = kinds(found, DELETED)
    assert len(deleted) == 1 and "=A2*10" in deleted[0]["problem"] and "#REF!" in deleted[0]["problem"], found

@case
def wrong_error_values_are_reported_in_one_finding():
    result = replay()
    result["samples"][4]["text"] = "0"
    result["samples"][3]["text"] = "#NAME?"
    found = findings(result)
    wrong = kinds(found, ERRORS)
    assert len(wrong) == 1, found
    problem = wrong[0]["problem"]
    assert "=SUM(F5:F6)" in problem and "=SUM(E1:E2" in problem and "“0”" in problem and "“#NAME?”" in problem, problem
    assert "=1/0" not in problem, problem

@case
def an_unverified_setup_reports_nothing():
    bad_setups = [
        replay(**{"setup.A1": {"text": "5", "formula": "5"}}),
        replay(**{"setup.A2": {"text": "20", "formula": "20"}}),
        replay(**{"setup.A3": {"text": "3", "formula": "=A1+1"}}),
        replay(**{"setup.A2": {"text": "0", "formula": "=A1*10"}}),
        replay(setup={}, error="no blank sheet of the probe's own"),
    ]
    for result in bad_setups:
        logs = []
        found = findings(result, logs=logs)
        assert found == [], (result["setup"], found)
        assert diag_logged(logs, "setup not verified"), logs

@case
def an_unverified_insert_reports_no_insert_finding():
    broken = {"afterInsert.A3": {"text": "0", "formula": "=A1*10"}}
    assert kinds(findings(replay(**broken)), SHIFT) and kinds(findings(replay(**broken)), DOLLAR)
    variants = [
        dict(broken, insert={"error": "command not found"}),
        dict(broken, insert=None),
        dict(broken, **{"afterInsert.A2": {"text": "5", "formula": "5"}}),
        dict(broken, **{"afterInsert.old": {"text": "2"}}),
        dict(broken, **{"afterInsert.A3": {"text": "", "formula": ""}}),
    ]
    for parts in variants:
        logs = []
        found = findings(replay(**parts), logs=logs)
        assert not kinds(found, SHIFT) and not kinds(found, DOLLAR) and not kinds(found, DELETED), (parts, found)
        assert diag_logged(logs, "insert not verified"), logs

@case
def the_dollar_finding_needs_the_absolute_sentence_and_a_stripped_formula():
    said = dict(statement(), absolute=False)
    assert not kinds(findings(P141, said=said), DOLLAR)
    for formula in ("=$A$2+1", "", None, "=B7+1", "=A4+1"):
        result = replay(**{"afterInsert.A4": {"text": "3", "formula": formula}})
        assert not kinds(findings(result), DOLLAR), formula

    assert kinds(findings(replay(**{"afterInsert.A4": {"text": "3", "formula": "=A1+1"}})), DOLLAR)

@case
def the_deleted_finding_needs_the_stated_error_and_a_verified_delete():
    repointed = {"afterDelete.A2": {"text": "0", "formula": "=A2*10"}}
    no_lost = dict(statement(), lost_sentence="")
    assert not kinds(findings(replay(**repointed), said=no_lost), DELETED)
    for parts in (dict(repointed, **{"del": {"error": "command not found"}}),
                  dict(repointed, **{"afterDelete.A2": {"text": "", "formula": ""}}),
                  dict(repointed, **{"afterDelete.A2": {"text": "7", "formula": "=B9+1"}}),
                  dict(repointed, **{"afterDelete.old": {"text": "2"}}),
                  dict(repointed, **{"afterDelete.old": None})):
        logs = []
        assert not kinds(findings(replay(**parts), logs=logs), DELETED), parts
        assert diag_logged(logs, "delete not verified"), logs

    for cell in ({"text": "0", "formula": "=#REF!*10"}, {"text": "#REF!", "formula": "=#REF!*10"},
                 {"text": "#ERR!", "formula": "=#ERR!*10"}):
        assert not kinds(findings(replay(**{"afterDelete.A2": cell})), DELETED), cell

    leak = kinds(findings(replay(**{"afterDelete.A2": {"text": "#REF!", "formula": "=A3*10"}})), DELETED)
    assert len(leak) == 1 and "=A3*10" in leak[0]["problem"], leak

    logs = []
    kept = findings(replay(**{"afterDelete.A2": {"text": "#REF!", "formula": "=A2*10"}}), logs=logs)
    assert not kinds(kept, DELETED), kept
    assert diag_logged(logs, "unchanged formula"), logs

    no_token = dict(statement(), lost_token="")
    for cell in ({"text": "#VALUE!", "formula": "=A2*10"}, {"text": "#X", "formula": "=A3*10"}):
        assert not kinds(findings(replay(**{"afterDelete.A2": cell}), said=no_token), DELETED), cell
    assert kinds(findings(replay(**repointed), said=no_token), DELETED)
    assert kinds(findings(replay(**{"afterDelete.A2": {"text": "0", "formula": "=A3*10"}}), said=no_token), DELETED)

@case
def error_samples_not_stored_as_typed_are_not_reported():
    for index, change in ((4, {"text": "0", "formula": "=SUM(F5:F6"}),
                          (4, {"text": "0", "formula": None}),
                          (4, {"text": "0", "before": "9"}),
                          (4, {"text": "0", "before": None})):
        result = replay()
        result["samples"][index].update(change)
        logs = []
        assert not kinds(findings(result, logs=logs), ERRORS), change
        assert diag_logged(logs, "not stored as typed"), logs
    result = replay()
    result["samples"].append({"cell": "Z9", "formula": "=1/0", "before": "", "text": "0"})
    result["samples"].append("garbage")
    assert not kinds(findings(result), ERRORS)

@case
def no_result_no_findings():
    from agent.structure_probe import structure_findings
    said = statement()
    for result in (None, "garbage", [], 3):
        logs = []
        assert structure_findings(said, job(said), result, log=logs.append) == [], result
        assert logs == [], logs
    assert structure_findings(None, job(said), P141, log=QUIET) == []
    assert structure_findings(said, None, P141, log=QUIET) == []

@case
def enabled_by_default_and_switched_off_by_env():
    from agent.structure_probe import enabled
    saved = os.environ.get("ARC_STRUCT_PROBE")
    try:
        os.environ.pop("ARC_STRUCT_PROBE", None)
        assert enabled() is True
        for off in ("0", "false", "off", " 0 "):
            os.environ["ARC_STRUCT_PROBE"] = off
            assert enabled() is False, off
        os.environ["ARC_STRUCT_PROBE"] = "1"
        assert enabled() is True
    finally:
        if saved is None:
            os.environ.pop("ARC_STRUCT_PROBE", None)
        else:
            os.environ["ARC_STRUCT_PROBE"] = saved

def browser_main() -> int:
    from agent.structure_probe import probe, probe_job, structure_findings, structure_statement
    app = os.environ.get("ARC_STRUCT_APP") or ""
    if not app or not (Path(app) / "backend" / "node_modules" / "@playwright" / "test").is_dir():
        print("skipped  browser probe: set ARC_STRUCT_APP to a built app copy with backend/node_modules/@playwright/test")
        return 0
    yaml = Path(app) / "requirements" / "requirements.yaml"
    if not yaml.is_file():
        print("skipped  browser probe: the app copy has no requirements/requirements.yaml")
        return 0
    try:
        said = structure_statement(yaml.read_text(encoding="utf-8"))
        planned = probe_job(said, None)
        if not planned:
            print("ok    browser probe: the requirement states no structural edit (nothing probed)")
            return 0
        result = probe(Path(app), planned, log=print)
        print(f"  result: {json.dumps(result)[:900]}")
        found = structure_findings(said, planned, result, log=print)
        for f in found:
            print(f"  finding: {f['problem'][:300]}")
        print(f"ok    browser probe: {len(found)} finding(s)")
        return 0
    except Exception:
        print("FAIL  browser probe")
        traceback.print_exc(limit=4)
        return 1

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
    print(f"{len(CASES) - failed}/{len(CASES)} v144h cases passed")
    if "--browser" in sys.argv:
        failed += browser_main()
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
