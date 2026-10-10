from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "selftests"))

import cases_v144h as H

CASES = []
QUIET = lambda message: None

def case(function):
    CASES.append(function)
    return function

SORT_DESC = ('Users select a rectangular data range in the current active worksheet and choose "Sort range" from the '
             '"Data" menu. A dialog named "Sort range" provides combo boxes labeled "Sort by" and "Order", a "Data has '
             'header row" checkbox, and a "Sort" button. Options in "Sort by" use the header text of the selected range as '
             'accessible names; "Order" provides options named "Ascending" and "Descending". When the first row is '
             'declared a header, it does not participate in sorting. Numbers, parseable dates, and text are compared '
             'according to their respective types; equal sort keys preserve their original relative order, and entire '
             'records move together by row. After sorting, the formula bar displays references and results consistent '
             'with the new positions, filtering and validation continue to apply to the same selected range, and data '
             'outside the selection remains unchanged; order and results persist after refresh. If sorting fails, an '
             'error is displayed and the grid retains its original order.')
SORT_SAID = "After sorting, the formula bar displays references and results consistent with the new positions"
PIVOT_DESC = ('Users select a source range containing headers and click "Create pivot table" in the "Data" menu. A dialog '
              'named "Create pivot table" displays visible text in the format "Source range: <cell range>", provides a "New '
              'worksheet" radio option and a "Create" button; when no pivot-result worksheet exists, the first unused '
              'PivotN name is used, so Pivot1 is created. A region named "Pivot table editor" provides combo boxes labeled '
              '"Rows", "Columns", "Values", and "Summarize by", plus an "Apply" button. Options for "Rows", "Columns", and '
              '"Values" use source header text as accessible names; "Summarize by" provides options named SUM, COUNT, and '
              'AVERAGE. After a successful apply, refreshing or reopening still shows the same pivot worksheet, field '
              'layout, summarization method, and results. The result worksheet provides a "Refresh pivot table" button; '
              'after source data or row/column changes, clicking refresh completely replaces the old summary using the '
              'current source range.')
PIVOT_SAID = "after source data or row/column changes, clicking refresh completely replaces the old summary using the current source range."

SHRINK = "When a deleted row or column is the first or last line of a range reference, the range shrinks accordingly."
FULL = H.SHEET + H.yaml_like(("REQ-5-2-1", SORT_DESC), ("REQ-5-3-1", PIVOT_DESC))
WITH_SHRINK = FULL + H.yaml_like(("REQ-X", SHRINK))
REAL_YAML = Path("local-data/local-eval/research/v144/v144h/apps/p141/requirements/requirements.yaml")

LIVE = json.loads(r'''{
 "axis": "row", "route": "/workbooks/2", "created": "New blank workbook", "bar": true,
 "setup": {"A1": {"text": "2", "formula": "2"}, "A2": {"text": "20", "formula": "=A1*10"},
  "A3": {"text": "3", "formula": "=$A$1+1"}, "D3": {"text": "12", "formula": "=SUM(D1:D2)"}},
 "insert": {"how": "right-click"},
 "afterInsert": {"old": {"text": ""}, "A2": {"text": "2", "formula": "2"}, "A3": {"text": "20", "formula": "=A2*10"},
  "A4": {"text": "3", "formula": "=A2+1"}, "D4": {"text": "12", "formula": "=SUM(D2:D3)"}},
 "del": {"how": "right-click"},
 "afterDelete": {"old": {"text": ""}, "A2": {"text": "#REF!", "formula": "=#REF!"},
  "A3": {"text": "#REF!", "formula": "=#REF!"}, "D3": {"text": "#REF!", "formula": "=#REF!"}},
 "samples": [
  {"cell": "F1", "formula": "=1/0", "before": "", "text": "#DIV/0!"},
  {"cell": "F2", "formula": "=NOSUCHFUNC(1)", "before": "", "text": "#NAME?"},
  {"cell": "F3", "formula": "=(1+2", "before": "", "text": "#ERROR!"},
  {"cell": "F4", "formula": "=SUM(E1:E2", "before": "", "text": "#ERROR!"},
  {"cell": "F5", "formula": "=SUM(F5:F6)", "before": "", "text": "#REF!"}],
 "visited": ["/"], "ms": 25165,
 "more": {
  "other_axis": {
   "axis": "column", "route": "/workbooks/3", "created": "New blank workbook", "bar": true,
   "setup": {"A1": {"text": "2", "formula": "2"}, "B1": {"text": "20", "formula": "=A1*10"},
    "C1": {"text": "3", "formula": "=$A$1+1"}, "C3": {"text": "12", "formula": "=SUM(A3:B3)"}},
   "insert": {"how": "right-click"},
   "afterInsert": {"old": {"text": ""}, "B1": {"text": "2", "formula": "2"}, "C1": {"text": "20", "formula": "=B1*10"},
    "D1": {"text": "3", "formula": "=B1+1"}, "D3": {"text": "12", "formula": "=SUM(B3:C3)"}},
   "del": {"how": "right-click"},
   "afterDelete": {"old": {"text": ""}, "B1": {"text": "#REF!", "formula": "=#REF!"},
    "C1": {"text": "#REF!", "formula": "=#REF!"}, "C3": {"text": "#REF!", "formula": "=#REF!"}},
   "samples": [], "visited": ["/"], "ms": 16815},
  "sort": {
   "axis": "row", "route": "/workbooks/4", "created": "New blank workbook", "bar": true,
   "setup": {"A2": {"text": "p1", "formula": "p1"}, "B2": {"text": "30", "formula": "30"}, "C2": {"text": "60", "formula": "=B2*2"},
    "A3": {"text": "p2", "formula": "p2"}, "B3": {"text": "10", "formula": "10"}, "C3": {"text": "20", "formula": "=B3*2"},
    "A4": {"text": "p3", "formula": "p3"}, "B4": {"text": "20", "formula": "20"}, "C4": {"text": "40", "formula": "=B4*2"}},
   "insert": null,
   "afterInsert": {"A2": {"text": "p2", "formula": "p2"}, "B2": {"text": "10", "formula": "10"}, "C2": {"text": "40", "formula": "=B3*2"},
    "A3": {"text": "p3", "formula": "p3"}, "B3": {"text": "20", "formula": "20"}, "C3": {"text": "60", "formula": "=B4*2"},
    "A4": {"text": "p1", "formula": "p1"}, "B4": {"text": "30", "formula": "30"}, "C4": {"text": "20", "formula": "=B2*2"}},
   "del": null, "afterDelete": {}, "samples": [], "visited": ["/"], "sorted": true, "alerts": [], "ms": 26487},
  "pivot": {
   "axis": "row", "route": "/workbooks/5", "created": "New blank workbook", "bar": true,
   "setup": {"A1": "Item", "B1": "SUM of Qty", "A2": "p1", "B2": "1", "A3": "p2", "B3": "2", "A4": "p3", "B4": "4",
    "A5": "Grand Total", "B5": "7", "A6": "", "B6": "", "A7": "", "B7": "", "A8": "", "B8": ""},
   "insert": {"how": "right-click"},
   "afterInsert": {"A1": "Item", "B1": "SUM of Qty", "A2": "p1", "B2": "1", "A3": "p4", "B3": "8", "A4": "p2", "B4": "2",
    "A5": "Grand Total", "B5": "11", "A6": "", "B6": "", "A7": "", "B7": "", "A8": "", "B8": ""},
   "del": null, "afterDelete": {}, "samples": [], "visited": ["/"], "tabs": {"src": 0, "piv": 1},
   "moved": {"A5": "p3", "A3": ""},
   "beforeRefresh": {"A1": "Item", "B1": "SUM of Qty", "A2": "p1", "B2": "1", "A3": "p2", "B3": "2", "A4": "p3", "B4": "4",
    "A5": "Grand Total", "B5": "7", "A6": "", "B6": "", "A7": "", "B7": "", "A8": "", "B8": ""},
   "refreshed": true, "alerts": [], "ms": 25013}}
}''')

L138B = json.loads(r'''{
 "axis": "row", "route": "/workbooks/2", "created": "New blank workbook", "bar": true,
 "setup": {"A1": {"text": "2", "formula": "2"}, "A2": {"text": "20", "formula": "=A1*10"}, "A3": {"text": "3", "formula": "=$A$1+1"}, "D3": {"text": "12", "formula": "=SUM(D1:D2)"}},
 "insert": {"how": "right-click"},
 "afterInsert": {"old": {"text": ""}, "A2": {"text": "2", "formula": "2"}, "A3": {"text": "20", "formula": "=A2*10"}, "A4": {"text": "3", "formula": "=$A$2+1"}, "D2": {"text": "5", "formula": "5"}, "D3": {"text": "7", "formula": "7"}, "D4": {"text": "12", "formula": "=SUM(D2:D3)"}},
 "del": {"how": "right-click"},
 "afterDelete": {"old": {"text": ""}, "A2": {"text": "#REF!", "formula": "=#REF!*10"}, "A3": {"text": "#REF!", "formula": "=#REF!+1"}, "D3": {"text": "#REF!", "formula": "=SUM(#REF!:D2)"}},
 "samples": [{"cell": "F1", "formula": "=1/0", "before": "", "text": "#DIV/0!"}, {"cell": "F2", "formula": "=NOSUCHFUNC(1)", "before": "", "text": "#NAME?"}, {"cell": "F3", "formula": "=(1+2", "before": "", "text": "#ERROR!"}, {"cell": "F4", "formula": "=SUM(E1:E2", "before": "", "text": "#ERROR!"}, {"cell": "F5", "formula": "=SUM(F5:F6)", "before": "", "text": "#REF!"}],
 "visited": ["/"], "ms": 25914,
 "more": {
  "other_axis": {
   "axis": "column", "route": "/workbooks/3", "created": "New blank workbook", "bar": true,
   "setup": {"A1": {"text": "2", "formula": "2"}, "B1": {"text": "20", "formula": "=A1*10"}, "C1": {"text": "3", "formula": "=$A$1+1"}, "C3": {"text": "12", "formula": "=SUM(A3:B3)"}},
   "insert": {"how": "right-click"},
   "afterInsert": {"old": {"text": ""}, "B1": {"text": "2", "formula": "2"}, "C1": {"text": "20", "formula": "=B1*10"}, "D1": {"text": "3", "formula": "=$B$1+1"}, "B3": {"text": "5", "formula": "5"}, "C3": {"text": "7", "formula": "7"}, "D3": {"text": "5", "formula": "=SUM(B3:A3)"}},
   "del": {"how": "right-click"},
   "afterDelete": {"old": {"text": ""}, "B1": {"text": "#REF!", "formula": "=#REF!*10"}, "C1": {"text": "#REF!", "formula": "=#REF!+1"}, "C3": {"text": "#REF!", "formula": "=SUM(#REF!:A3)"}},
   "samples": [], "visited": ["/"], "ms": 17550},
  "sort": {
   "axis": "row", "route": "/workbooks/4", "created": "New blank workbook", "bar": true,
   "setup": {"A2": {"text": "p1", "formula": "p1"}, "B2": {"text": "30", "formula": "30"}, "C2": {"text": "60", "formula": "=B2*2"}, "A3": {"text": "p2", "formula": "p2"}, "B3": {"text": "10", "formula": "10"}, "C3": {"text": "20", "formula": "=B3*2"}, "A4": {"text": "p3", "formula": "p3"}, "B4": {"text": "20", "formula": "20"}, "C4": {"text": "40", "formula": "=B4*2"}},
   "insert": null,
   "afterInsert": {"A2": {"text": "p1", "formula": "p1"}, "B2": {"text": "30", "formula": "30"}, "C2": {"text": "60", "formula": "=B2*2"}, "A3": {"text": "p2", "formula": "p2"}, "B3": {"text": "10", "formula": "10"}, "C3": {"text": "20", "formula": "=B3*2"}, "A4": {"text": "p3", "formula": "p3"}, "B4": {"text": "20", "formula": "20"}, "C4": {"text": "40", "formula": "=B4*2"}},
   "del": null, "afterDelete": {}, "samples": [], "visited": ["/"], "sorted": true, "alerts": [], "ms": 26271},
  "pivot": {
   "axis": "row", "route": "/workbooks/5", "created": "New blank workbook", "bar": true,
   "setup": {"A1": "Item", "B1": "SUM of Qty", "A2": "p1", "B2": "1", "A3": "p2", "B3": "2", "A4": "p3", "B4": "4", "A5": "Grand Total", "B5": "7", "A6": "", "B6": "", "A7": "", "B7": "", "A8": "", "B8": ""},
   "insert": {"how": "right-click"},
   "afterInsert": {"A1": "Item", "B1": "SUM of Qty", "A2": "p1", "B2": "1", "A3": "p4", "B3": "8", "A4": "p2", "B4": "2", "A5": "Grand Total", "B5": "11", "A6": "", "B6": "", "A7": "", "B7": "", "A8": "", "B8": ""},
   "del": null, "afterDelete": {}, "samples": [], "visited": ["/"], "tabs": {"src": 0, "piv": 1},
   "moved": {"A5": "p3", "A3": ""},
   "beforeRefresh": {"A1": "Item", "B1": "SUM of Qty", "A2": "p1", "B2": "1", "A3": "p2", "B3": "2", "A4": "p3", "B4": "4", "A5": "Grand Total", "B5": "7", "A6": "", "B6": "", "A7": "", "B7": "", "A8": "", "B8": ""},
   "refreshed": true, "alerts": [], "ms": 25230}}
}''')

DOLLAR = "`$` markers removed by a"
SHIFT = "References not shifted on insert"
SORTED = "Sorted formulas keep their old references"
PIVOT = "Pivot source range not adjusted"
RANGE = "A range lost its end"
DIAG = "(diagnostic, not reported)"
ENV = ("ARC_STRUCT_COLUMNS", "ARC_STRUCT_SORT", "ARC_STRUCT_PIVOT")

def kinds(found, prefix):
    return [f for f in found if f["problem"].startswith(prefix)]

def statement(text=FULL):
    from agent.structure_probe import structure_statement
    return structure_statement(text)

def job(said=None):
    from agent.structure_probe import probe_job
    return probe_job(said or statement(), None)

def live(**changes):
    result = json.loads(json.dumps(LIVE))
    drop = changes.pop("drop", ())
    for key, value in changes.items():
        *path, last = key.split(".")
        at = result
        for part in path:
            at = at[part]
        at[last] = value
    for key in drop:
        *path, last = key.split(".")
        at = result
        for part in path:
            at = at[part]
        at.pop(last, None)
    return result

def findings(result, said=None, logs=None, planned=None):
    from agent.structure_probe import structure_findings
    said = said or statement()
    return structure_findings(said, planned or job(said), result, log=(logs.append if logs is not None else QUIET))

def diag_logged(logs, words):
    return any(DIAG in line and words in line for line in logs)

class Env:

    def __init__(self, **values):
        self.values, self.saved = values, {}

    def __enter__(self):
        for name in ENV:
            self.saved[name] = os.environ.get(name)
            os.environ.pop(name, None)
        for name, value in self.values.items():
            os.environ[name] = value
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

@case
def the_full_statement_names_the_other_axis_the_sort_and_the_pivot():
    said = statement()
    assert said["axis"] == "row", said
    assert said["other_axis"] == {"axis": "column", "header": "A", "insert": "Insert 1 column left",
                                  "delete": "Delete column"}, said["other_axis"]
    assert {k: v for k, v in said["sort"].items() if k != "sentence"} == {
        "menu": "Data", "command": "Sort range", "by": "Sort by", "order": "Order", "ascending": "Ascending",
        "header_box": "Data has header row", "button": "Sort"}, said["sort"]
    assert said["sort"]["sentence"].startswith(SORT_SAID), said["sort"]
    assert said["pivot"] == {"sentence": PIVOT_SAID, "menu": "Data", "command": "Create pivot table",
                             "create_button": "Create", "rows": "Rows", "values": "Values", "method": "Summarize by",
                             "sum": "SUM", "apply": "Apply", "refresh": "Refresh pivot table",
                             "insert": "Insert 1 row above"}, said["pivot"]
    assert said["shrink_sentence"] == "", said["shrink_sentence"]

@case
def the_real_p141_requirement_gives_the_same_parts():
    if not REAL_YAML.is_file():
        print("  skipped: the p141 requirement is not present")
        return
    said = statement(REAL_YAML.read_text(encoding="utf-8"))
    ours = statement()
    assert said["other_axis"] == ours["other_axis"], said["other_axis"]
    assert {k: v for k, v in said["sort"].items() if k != "sentence"} == {k: v for k, v in ours["sort"].items() if k != "sentence"}
    assert said["sort"]["sentence"].startswith(SORT_SAID), said["sort"]
    assert said["pivot"] == ours["pivot"], said["pivot"]
    assert said["shrink_sentence"] == "", said["shrink_sentence"]
    found = findings(LIVE, said=said)
    assert [f["problem"][:30] for f in found] == [f["problem"][:30] for f in findings(LIVE)], found

@case
def shrink_is_recognised_only_from_a_ranges_shrink_sentence():
    said = statement(WITH_SHRINK)
    assert said["shrink_sentence"].endswith(SHRINK), said["shrink_sentence"]
    for other in ("When Escape is pressed the selection shrinks back to one cell.",
                  "A range reference keeps its end rows when a row is deleted.",
                  "The range grows when a row is inserted inside it."):
        assert statement(FULL + H.yaml_like(("REQ-X", other)))["shrink_sentence"] == "", other
    for verb in ("contracts", "narrows", "shrinks"):
        text = FULL + H.yaml_like(("REQ-X", f"A range {verb} when its last row is deleted."))
        assert statement(text)["shrink_sentence"], verb

@case
def the_other_axis_needs_both_quoted_commands_of_that_axis():
    rows_only = H.yaml_like(("REQ-2-2-1", H.ROWS_DESC), ("REQ-4", H.FORMULA_DESC))
    assert statement(rows_only)["other_axis"] is None
    cols_only = H.yaml_like(("REQ-2-2-2", H.COLS_DESC))
    said = statement(cols_only)
    assert said["axis"] == "column" and said["other_axis"] is None, said
    no_delete = H.COLS_DESC.replace('"Delete column"', "Delete column")
    said = statement(H.yaml_like(("REQ-2-2-1", H.ROWS_DESC), ("REQ-2-2-2", no_delete)))
    assert said["axis"] == "row" and said["other_axis"] is None, said

@case
def the_sort_needs_its_sentence_and_every_quoted_name():
    assert statement()["sort"]
    variants = {
        "no sentence": SORT_DESC.replace(SORT_SAID + ",", "Afterwards,"),
        "command unquoted": SORT_DESC.replace('"Sort range"', "Sort range"),
        "no key box": SORT_DESC.replace('"Sort by"', "Sort by"),
        "no order box": SORT_DESC.replace('"Order"', "Order"),
        "no ascending": SORT_DESC.replace('"Ascending"', "Ascending"),
        "no header checkbox": SORT_DESC.replace('"Data has header row" checkbox', "header checkbox"),
        "no button": SORT_DESC.replace('"Sort" button', "Sort button"),
    }
    for name, desc in variants.items():
        said = statement(H.SHEET + H.yaml_like(("REQ-5-2-1", desc), ("REQ-5-3-1", PIVOT_DESC)))
        assert said is not None and said["sort"] is None, (name, said and said["sort"])
        assert said["pivot"], name
    assert statement(H.SHEET)["sort"] is None

    early = 'The "Sort by" box and the "Sort" button are described below. ' + SORT_DESC
    said = statement(H.SHEET + H.yaml_like(("REQ-5-2-1", early), ("REQ-5-3-1", PIVOT_DESC)))
    assert said["sort"] and said["sort"]["command"] == "Sort range" and said["sort"]["by"] == "Sort by", said["sort"]

@case
def the_pivot_needs_its_sentence_an_insert_row_command_and_every_quoted_name():
    assert statement()["pivot"]
    variants = {
        "no sentence": PIVOT_DESC.replace(PIVOT_SAID, "then it is done."),
        "refresh unquoted": PIVOT_DESC.replace('"Refresh pivot table"', "Refresh pivot table"),
        "command unquoted": PIVOT_DESC.replace('"Create pivot table"', "Create pivot table"),
        "no create button": PIVOT_DESC.replace('"Create" button', "Create button"),
        "no rows box": PIVOT_DESC.replace('"Rows"', "Rows"),
        "no values box": PIVOT_DESC.replace('"Values"', "Values"),
        "no apply": PIVOT_DESC.replace('"Apply" button', "Apply button"),
        "method without SUM": PIVOT_DESC.replace("SUM, COUNT, and AVERAGE", "COUNT and AVERAGE"),
    }
    for name, desc in variants.items():
        said = statement(H.SHEET + H.yaml_like(("REQ-5-2-1", SORT_DESC), ("REQ-5-3-1", desc)))
        assert said is not None and said["pivot"] is None, (name, said and said["pivot"])
        assert said["sort"], name

    cols = statement(H.yaml_like(("REQ-2-2-2", H.COLS_DESC), ("REQ-5-3-1", PIVOT_DESC)))
    assert cols["axis"] == "column" and cols["pivot"] is None, cols["pivot"]
    assert statement(H.SHEET)["pivot"] is None

@case
def the_job_plans_the_range_and_three_sub_jobs():
    with Env():
        planned = job()
    plan = planned["plan"]
    assert (plan["r_first"], plan["r_last"], plan["r_sum"], plan["r_formula"]) == ("D1", "D2", "D3", "=SUM(D1:D2)"), plan
    assert (plan["i_r_sum"], plan["i_r_expect"], plan["d_r_sum"]) == ("D4", "=SUM(D2:D3)", "D3"), plan
    assert {"D1", "D2", "D3", "D4"} <= set(planned["used"]), planned["used"]
    assert [s["name"] for s in planned["more"]] == ["other_axis", "sort", "pivot"], planned["more"]
    other, sort, pivot = planned["more"]
    assert (other["axis"], other["header"], other["insert"], other["delete"]) == ("column", "A", "Insert 1 column left",
                                                                                   "Delete column"), other
    assert other["samples"] == [] and "more" not in other, other
    op = other["plan"]
    assert (op["value"], op["rel"], op["abs"], op["i_abs_expect"]) == ("A1", "B1", "C1", "=$B$1+1"), op
    assert (op["r_first"], op["r_last"], op["r_sum"], op["r_formula"]) == ("A3", "B3", "C3", "=SUM(A3:B3)"), op
    assert (op["i_r_sum"], op["i_r_expect"], op["d_r_sum"], op["delete_header"]) == ("D3", "=SUM(B3:C3)", "C3", "B"), op
    for sub in (other, sort, pivot):
        assert sub["bar"] == "Formula bar" and sub["create"][0] == "New blank workbook" and sub["submit"] == "Create", sub
    assert sort["mode"] == "sort" and sort["command"] == "Sort range" and sort["menu"] == "Data", sort
    assert ("C2", "=B2*2") in sort["cells"] and ("A1", "Item") in sort["cells"] and ("B4", "20") in sort["cells"], sort
    assert sort["range"] == ["A1", "C4"] and sort["key"] == "Qty" and len(sort["read"]) == 9 and len(sort["used"]) == 12, sort
    assert pivot["mode"] == "pivot" and pivot["insert"] == "Insert 1 row above" and pivot["at"] == "3", pivot
    assert pivot["range"] == ["A1", "B4"] and pivot["key"] == "Item" and pivot["value"] == "Qty", pivot
    assert [tuple(a) for a in pivot["added"]] == [("A3", "p4"), ("B3", "8")] and pivot["moved"] == "A5", pivot
    assert pivot["refresh"] == "Refresh pivot table" and pivot["apply"] == "Apply" and pivot["sum"] == "SUM", pivot
    json.dumps(planned)

@case
def env_switches_drop_one_sub_job_each():
    names = lambda planned: [s["name"] for s in planned.get("more") or []]
    with Env():
        assert names(job()) == ["other_axis", "sort", "pivot"]
    with Env(ARC_STRUCT_COLUMNS="1", ARC_STRUCT_SORT="yes"):
        assert names(job()) == ["other_axis", "sort", "pivot"]
    for off in ("0", "false", "off", " 0 "):
        with Env(ARC_STRUCT_COLUMNS=off):
            assert names(job()) == ["sort", "pivot"], off
        with Env(ARC_STRUCT_SORT=off):
            assert names(job()) == ["other_axis", "pivot"], off
        with Env(ARC_STRUCT_PIVOT=off):
            assert names(job()) == ["other_axis", "sort"], off
    with Env(ARC_STRUCT_COLUMNS="0", ARC_STRUCT_SORT="0", ARC_STRUCT_PIVOT="0"):
        planned = job()
        assert "more" not in planned and planned["plan"]["r_sum"] == "D3", planned.keys()

@case
def a_statement_without_parts_plans_no_sub_jobs():
    with Env():
        assert "more" not in job(statement(H.yaml_like(("REQ-2-2-1", H.ROWS_DESC))))
        said = dict(statement(), other_axis=None, sort=None, pivot=None)
        assert "more" not in job(said)
        assert [s["name"] for s in job(dict(statement(), sort=None))["more"]] == ["other_axis", "pivot"]

@case
def probe_runs_each_sub_job_on_its_own():
    from agent import fsutil
    from agent import structure_probe as sp
    names = ("BackgroundProcess", "free_port", "fresh_data_env", "npm_bin", "wait_http")
    saved = {n: getattr(fsutil, n) for n in names}
    saved_run = sp.run_probe
    calls, stopped, logs = [], [], []

    class Server:
        def __init__(self, *args, **kwargs):
            pass

        def stop(self):
            stopped.append(1)

    def fake_run(backend, base, sub, log=print):
        calls.append(sub)
        if sub.get("name") == "sort":
            raise RuntimeError("boom")
        if sub.get("name") == "pivot":
            return None
        return {"route": "/r", "who": sub.get("name") or "main"}

    tmp = Path(tempfile.mkdtemp(prefix="v144o-"))
    try:
        (tmp / "backend" / "node_modules" / "@playwright" / "test").mkdir(parents=True)
        fsutil.BackgroundProcess, fsutil.free_port = Server, (lambda preferred=3000: 3999)
        fsutil.fresh_data_env, fsutil.npm_bin = (lambda project_dir: {}), (lambda name: name)
        fsutil.wait_http = lambda url, timeout=60: (True, 200)
        sp.run_probe = fake_run
        with Env():
            planned = job()
        result = sp.probe(tmp, planned, log=logs.append)
        assert "more" not in calls[0] and calls[0]["plan"] == planned["plan"], calls[0].keys()
        assert [c.get("name") for c in calls] == [None, "other_axis", "sort", "pivot"], calls
        assert result["who"] == "main" and result["more"] == {"other_axis": {"route": "/r", "who": "other_axis"},
                                                               "pivot": None}, result
        assert any("sort" in line and "crashed" in line for line in logs), logs
        assert stopped == [1]

        calls.clear()
        sp.run_probe = lambda backend, base, sub, log=print: calls.append(sub) or None
        assert sp.probe(tmp, planned, log=QUIET) is None and len(calls) == 1, calls

        calls.clear()
        assert sp.probe(tmp / "nowhere", planned, log=QUIET) is None and calls == []
    finally:
        for n, v in saved.items():
            setattr(fsutil, n, v)
        sp.run_probe = saved_run
        shutil.rmtree(tmp, ignore_errors=True)

@case
def the_live_replay_gives_exactly_the_four_findings_in_order():
    logs = []
    with Env():
        found = findings(LIVE, logs=logs)
    assert [f["where"] for f in found] == ["route /workbooks/2", "route /workbooks/4", "route /workbooks/5",
                                           "route /workbooks/3"], found
    assert found[0]["problem"].startswith(DOLLAR + " row insert"), found[0]
    assert found[1]["problem"].startswith(SORTED) and found[2]["problem"].startswith(PIVOT), found
    assert found[3]["problem"].startswith(DOLLAR + " column insert"), found[3]
    sort = found[1]["problem"]
    for moved in ("p1 (30) moved from row 2 to row 4", "“=B2*2”", "p2 (10) moved from row 3 to row 2", "“=B3*2”",
                  "p3 (20) moved from row 4 to row 3", "“=B4*2”", "A1:C4", "“Sort range”"):
        assert moved in sort, (moved, sort)
    assert SORT_SAID in found[1]["requirement"], found[1]
    pivot = found[2]["problem"]
    assert "p4 appears but p3" in pivot and "A1:B4" in pivot and "“Refresh pivot table”" in pivot and "A5" in pivot, pivot
    assert all(f["fix"] and f["requirement"] for f in found), found

    assert not kinds(found, RANGE)
    assert sum(1 for line in logs if DIAG in line and "does not say ranges shrink" in line) == 2, logs

@case
def the_column_findings_use_the_column_sub_statement():
    with Env():
        column = findings(LIVE)[3]
    problem = column["problem"]
    for words in ("`$` markers removed by a column insert", "“Insert 1 column left” on column A", "=$A$1+1 in C1",
                  "moved to D1", "“=B1+1”", "expected =$B$1+1"):
        assert words in problem, (words, problem)
    assert " row" not in problem, problem
    assert column["where"] == "route /workbooks/3", column

    with Env():
        found = findings(live(**{"more.other_axis.afterInsert.C1": {"text": "0", "formula": "=A1*10"}}))
    shifted = kinds(found, SHIFT)
    assert len(shifted) == 1 and shifted[0]["where"] == "route /workbooks/3", found
    assert "“Insert 1 column left” on column A" in shifted[0]["problem"] and "expected =B1*10" in shifted[0]["problem"], shifted

    logs = []
    with Env():
        found = findings(live(**{"more.other_axis.setup.B1": {"text": "20", "formula": "20"}}), logs=logs)
    assert [f["where"] for f in found] == ["route /workbooks/2", "route /workbooks/4", "route /workbooks/5"], found
    assert diag_logged(logs, "setup not verified"), logs

@case
def g5_is_a_finding_only_with_a_ranges_shrink_sentence():
    said = statement(WITH_SHRINK)
    with Env():
        planned = job(said)
        logs = []
        found = findings(LIVE, said=said, logs=logs, planned=planned)
    order = [DOLLAR, RANGE, SORTED, PIVOT, DOLLAR, RANGE]
    assert len(found) == len(order) and all(f["problem"].startswith(p) for f, p in zip(found, order)), found
    row, col = kinds(found, RANGE)
    assert "=SUM(D2:D3)" in row["problem"] and "moved to D3" in row["problem"] and "“=#REF!”" in row["problem"], row
    assert row["where"] == "route /workbooks/2" and SHRINK in row["requirement"], row
    assert "end column" in col["problem"] and "=SUM(B3:C3)" in col["problem"] and col["where"] == "route /workbooks/3", col
    assert not any("does not say ranges shrink" in line for line in logs), logs

    for parts in ({"afterDelete.D3": {"text": "7", "formula": "=SUM(D2:D2)"}},
                  {"setup.D3": {"text": "0", "formula": "=SUM(D1:D2)"}},
                  {"setup.D3": {"text": "12", "formula": "12"}},
                  {"afterInsert.D4": {"text": "12", "formula": "=SUM(D1:D2)"}},
                  {"del": {"error": "command not found"}}):
        with Env():
            found = findings(live(**parts), said=said, planned=planned)
        assert len(kinds(found, RANGE)) == 1 and kinds(found, RANGE)[0]["where"] == "route /workbooks/3", (parts, found)
    with Env():
        found = findings(live(drop=["setup.D3"]), said=said, planned=planned)
    assert len(kinds(found, RANGE)) == 1, found

BROKEN = "Range reference broken by a"

L138_COLUMN = {"more.other_axis.afterInsert.B3": {"text": "5", "formula": "5"},
               "more.other_axis.afterInsert.C3": {"text": "7", "formula": "7"},
               "more.other_axis.afterInsert.D3": {"text": "5", "formula": "=SUM(B3:A3)"}}
ROW_BROKEN = {"afterInsert.D2": {"text": "5", "formula": "5"}, "afterInsert.D3": {"text": "7", "formula": "7"},
              "afterInsert.D4": {"text": "5", "formula": "=SUM(D1:D2)"}}

@case
def a_range_broken_by_the_insert_is_reported_for_either_axis():
    with Env():
        planned = job()
    plan, op = planned["plan"], planned["more"][0]["plan"]
    assert (plan["i_r_first"], plan["i_r_last"]) == ("D2", "D3") and (op["i_r_first"], op["i_r_last"]) == ("B3", "C3"), (plan, op)
    logs = []
    with Env():
        found = findings(live(**L138_COLUMN), logs=logs)
    broken = kinds(found, BROKEN)
    assert len(broken) == 1 and broken[0]["problem"].startswith(BROKEN + " column insert"), found
    problem = broken[0]["problem"]
    for words in ("“=SUM(B3:A3)”", "shows “5”", "expected =SUM(B3:C3) = 12", "moved to B3 / C3", "=SUM(A3:B3) in C3",
                  "“Insert 1 column left” on column A"):
        assert words in problem, (words, problem)
    assert broken[0]["where"] == "route /workbooks/3" and broken[0]["fix"] and broken[0]["requirement"], broken
    assert len(found) == 5 and found[-1] is broken[0] and found[-2]["problem"].startswith(DOLLAR + " column"), found
    with Env():
        row = kinds(findings(live(**ROW_BROKEN)), BROKEN)
    assert len(row) == 1 and row[0]["problem"].startswith(BROKEN + " row insert") and "“=SUM(D1:D2)”" in row[0]["problem"], row
    assert row[0]["where"] == "route /workbooks/2", row

@case
def the_l138b_replay_gives_the_pivot_and_the_column_range_findings():
    logs = []
    with Env():
        found = findings(L138B, logs=logs)
    assert [(f["where"], f["problem"][:31]) for f in found] == [
        ("route /workbooks/5", PIVOT[:31]), ("route /workbooks/3", (BROKEN + " column insert")[:31])], found
    assert "“=SUM(B3:A3)”" in found[1]["problem"] and "shows “5”" in found[1]["problem"], found[1]

    assert diag_logged(logs, "sort not verified"), logs
    assert diag_logged(logs, "=SUM(#REF!:D2)") and diag_logged(logs, "does not say ranges shrink"), logs

@case
def a_range_insert_finding_needs_both_moved_values_and_a_wrong_sum():

    with Env():
        assert not kinds(findings(LIVE), BROKEN)
    variants = [
        {"afterInsert.D2": {"text": "4", "formula": "4"}},
        {"afterInsert.D3": {"text": "", "formula": ""}},
        {"afterInsert.D4": {"text": "#REF!", "formula": "=#REF!"}},
        {"afterInsert.D4": {"text": "7", "formula": "=SUM(#REF!:D3)"}},
        {"afterInsert.D4": {"text": "5", "formula": "5"}},
        {"afterInsert.D4": {"text": "12", "formula": "=SUM($D$2:$D$3)"}},
        {"afterInsert.D4": {"text": "", "formula": "=SUM(D1:D2)"}},
        {"afterInsert.D4": {"text": "5", "formula": "=SUM(D2:D3)"}},
        {"setup.D3": {"text": "0", "formula": "=SUM(D1:D2)"}},
        {"setup.D3": {"text": "12", "formula": "12"}},
        {"insert": {"error": "command not found"}},
        {"afterInsert.old": {"text": "2"}},
        {"afterInsert.A2": {"text": "9", "formula": "9"}},
    ]
    for parts in variants:
        with Env():
            found = findings(live(**dict(ROW_BROKEN, **parts)))
        assert not kinds(found, BROKEN), (parts, found)
    with Env():
        assert not kinds(findings(live(**dict(ROW_BROKEN), drop=["setup.D3"])), BROKEN)

@case
def the_sort_finding_needs_moved_records_whose_formulas_kept_old_rows():
    rewritten = {"more.sort.afterInsert.C2": {"text": "20", "formula": "=B2*2"},
                 "more.sort.afterInsert.C3": {"text": "40", "formula": "=B3*2"},
                 "more.sort.afterInsert.C4": {"text": "60", "formula": "=B4*2"}}
    logs = []
    with Env():
        found = findings(live(**rewritten), logs=logs)
    assert not kinds(found, SORTED) and len(found) == 3, found
    assert not diag_logged(logs, "sort not verified"), logs

    with Env():
        one = kinds(findings(live(**dict(rewritten, **{"more.sort.afterInsert.C4": {"text": "60", "formula": "=B2*2"}}))), SORTED)
    assert len(one) == 1 and "p1 (30) moved from row 2 to row 4" in one[0]["problem"], one
    assert "p2 (10)" not in one[0]["problem"] and "p3 (20)" not in one[0]["problem"], one

@case
def a_record_that_did_not_move_is_never_stale():
    from agent import structure_probe as sp
    saved = sp.SORT_ROWS
    try:
        sp.SORT_ROWS = [("p1", 10), ("p2", 30), ("p3", 20)]
        sort = {"setup": {}, "afterInsert": {}, "sorted": True, "route": "/w"}
        for r, (key, qty) in enumerate(sp.SORT_ROWS, start=2):
            sort["setup"].update({f"A{r}": {"text": key}, f"B{r}": {"text": str(qty)},
                                  f"C{r}": {"text": str(2 * qty), "formula": f"=B{r}*2"}})
        for r, (key, qty) in enumerate(sorted(sp.SORT_ROWS, key=lambda kv: kv[1]), start=2):
            sort["afterInsert"].update({f"A{r}": {"text": key}, f"B{r}": {"text": str(qty)},
                                        f"C{r}": {"text": str(2 * qty), "formula": f"=B{r}*2"}})
        with Env():
            said = statement()
            planned = job(said)
        result = live(**{"more.sort": sort})
        logs = []
        found = findings(result, said=said, logs=logs, planned=planned)
        assert not kinds(found, SORTED), found
        assert not diag_logged(logs, "sort not verified"), logs

        sort["afterInsert"]["C3"] = {"text": "60", "formula": "=B4*2"}
        sort["afterInsert"]["C4"] = {"text": "20", "formula": "=B3*2"}
        stale = kinds(findings(live(**{"more.sort": sort}), said=said, planned=planned), SORTED)
        assert len(stale) == 1 and "p3 (20) moved from row 4 to row 3" in stale[0]["problem"], stale
        assert "p2 (30) moved from row 3 to row 4" in stale[0]["problem"] and "p1" not in stale[0]["problem"], stale
    finally:
        sp.SORT_ROWS = saved

@case
def an_unverified_sort_reports_nothing_and_logs_it():
    after = LIVE["more"]["sort"]["afterInsert"]
    unmoved = {f"more.sort.afterInsert.{c}": LIVE["more"]["sort"]["setup"][c] for c in after}
    variants = [
        unmoved,
        {"more.sort.sorted": False},
        {"more.sort.sorted": None, "more.sort.error": "no command \"Sort range\""},
        {"more.sort.setup.C2": {"text": "60", "formula": "60"}},
        {"more.sort.setup.C3": {"text": "0", "formula": "=B3*2"}},
        {"more.sort.afterInsert.B2": {"text": "11", "formula": "11"}},
        {"more.sort.afterInsert.A4": {"text": "p9", "formula": "p9"}},
        {"more.sort.setup": {}},
        {"more.sort.afterInsert": None},
    ]
    for parts in variants:
        logs = []
        with Env():
            found = findings(live(**parts), logs=logs)
        assert not kinds(found, SORTED) and len(found) == 3, (parts, found)
        assert diag_logged(logs, "sort not verified"), (parts, logs)

@case
def the_pivot_finding_needs_a_refresh_that_lost_the_last_record():
    kept = {"more.pivot.afterInsert": {"A1": "Item", "B1": "SUM of Qty", "A2": "p1", "B2": "1", "A3": "p4", "B3": "8",
                                       "A4": "p2", "B4": "2", "A5": "p3", "B5": "4", "A6": "Grand Total", "B6": "15"}}
    logs = []
    with Env():
        found = findings(live(**kept), logs=logs)
    assert not kinds(found, PIVOT) and len(found) == 3, found
    assert diag_logged(logs, "applied=True inserted=True refreshed=True"), logs
    unrefreshed = [
        {"more.pivot.afterInsert": LIVE["more"]["pivot"]["beforeRefresh"]},
        {"more.pivot.refreshed": False},
        {"more.pivot.refreshed": None, "more.pivot.error": "no button \"Refresh pivot table\""},

        {"more.pivot.afterInsert": {"A1": "Item", "A2": "p1", "A3": "p2", "A4": "Grand Total", "B4": "3"}},
        {"more.pivot.afterInsert": {}},
    ]
    for parts in unrefreshed:
        logs = []
        with Env():
            found = findings(live(**parts), logs=logs)
        assert not kinds(found, PIVOT) and len(found) == 3, (parts, found)
        assert diag_logged(logs, "refreshed=False"), (parts, logs)

@case
def an_unverified_pivot_insert_or_apply_reports_nothing_and_logs_it():
    not_inserted = [
        {"more.pivot.insert": {"error": "command \"Insert 1 row above\" not found"}},
        {"more.pivot.insert": None},
        {"more.pivot.insert": {}},
        {"more.pivot.moved": {"A5": "", "A3": ""}},
        {"more.pivot.moved": {"A5": "p3", "A3": "p3"}},
        {"more.pivot.moved": None},
    ]
    for parts in not_inserted:
        logs = []
        with Env():
            found = findings(live(**parts), logs=logs)
        assert not kinds(found, PIVOT) and len(found) == 3, (parts, found)
        assert diag_logged(logs, "inserted=False"), (parts, logs)
    not_applied = [
        {"more.pivot.setup": {"A1": "Item", "A2": "p1", "A3": "p2"}},
        {"more.pivot.setup": {}},
        {"more.pivot.setup": None},
    ]
    for parts in not_applied:
        logs = []
        with Env():
            found = findings(live(**parts), logs=logs)
        assert not kinds(found, PIVOT) and len(found) == 3, (parts, found)
        assert diag_logged(logs, "applied=False"), (parts, logs)

@case
def missing_or_broken_sub_results_give_the_first_run_findings_only():
    for result in (live(drop=["more"]), live(more=None), live(more="garbage"), live(more=[]),
                   live(more={"other_axis": None, "sort": None, "pivot": None}),
                   live(more={"other_axis": "x", "sort": 3, "pivot": []})):
        logs = []
        with Env():
            found = findings(result, logs=logs)
        assert [f["where"] for f in found] == ["route /workbooks/2"] and kinds(found, DOLLAR), (result.get("more"), found)

    logs = []
    with Env():
        found = findings(live(more={"other_axis": {}, "sort": {}, "pivot": {}}), logs=logs)
    assert [f["where"] for f in found] == ["route /workbooks/2"], found
    for words in ("setup not verified", "sort not verified", "pivot refresh not judged"):
        assert diag_logged(logs, words), (words, logs)

    with Env():
        planned = job()
    bare = {k: v for k, v in planned.items() if k != "more"}
    assert len(findings(LIVE, planned=bare)) == 1
    said = dict(statement(), sort=None, pivot=None, other_axis=None)
    assert len(findings(LIVE, said=said, planned=planned)) == 1

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
    print(f"{len(CASES) - failed}/{len(CASES)} v144o cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
