from __future__ import annotations

import json
import os
import sys
import traceback
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import structure_probe as sp
import cases_v144h as H
import cases_v146d as G

CASES = []
QUIET = lambda message: None
ABS = "Absolute reference not moved by a"
DOLLAR = "`$` markers removed by a"
INTERIOR = "Range broken by deleting a"
DIAG = "(diagnostic, not reported)"

def case(function):
    CASES.append(function)
    return function

class Env:

    def __init__(self, **values: str) -> None:
        self.values, self.saved = values, {}

    def __enter__(self):
        for key in [k for k in os.environ if k.startswith("ARC_STRUCT_")] + list(self.values):
            self.saved.setdefault(key, os.environ.get(key))
            os.environ.pop(key, None)
        os.environ.update(self.values)
        return self

    def __exit__(self, *exc):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

COPY_RULE = ('When a formula cell is copied through REQ-3-2-1 to another location in the same worksheet, relative row and '
             'column references in the target formula bar change according to the target offset while absolute '
             'references remain unchanged; the formula bar displays the adjusted original formula.')
SHEET = H.SHEET + H.yaml_like(("REQ-4-1-2", COPY_RULE))

def statement(text: str = SHEET) -> dict:
    said = sp.structure_statement(text)
    assert said, said
    return said

def job(said=None) -> dict:
    with Env():
        return sp.probe_job(said or statement(), None)

CORRECT = {
    "axis": "row", "route": "/workbooks/2", "created": "New blank workbook", "bar": True,
    "setup": {"A1": {"text": "2", "formula": "2"}, "A2": {"text": "20", "formula": "=A1*10"},
              "A3": {"text": "3", "formula": "=$A$1+1"}, "D3": {"text": "12", "formula": "=SUM(D1:D2)"},
              "A4": {"text": "7", "formula": "=A$1+5"}},
    "insert": {"how": "right-click"},
    "afterInsert": {"old": {"text": ""}, "A2": {"text": "2", "formula": "2"}, "A3": {"text": "20", "formula": "=A2*10"},
                    "A4": {"text": "3", "formula": "=$A$2+1"}, "D2": {"text": "5", "formula": "5"},
                    "D3": {"text": "7", "formula": "7"}, "D4": {"text": "12", "formula": "=SUM(D2:D3)"},
                    "A5": {"text": "7", "formula": "=A$2+5"}},
    "del": {"how": "right-click"},
    "afterDelete": {"old": {"text": ""}, "A2": {"text": "#REF!", "formula": "=#REF!*10"},
                    "A3": {"text": "#REF!", "formula": "=#REF!+1"}, "D3": {"text": "7", "formula": "=SUM(D2:D2)"}},
    "interior": {"moved": {"E1": "", "E2": "2", "E3": "4"}, "sum": {"text": "7", "formula": "=SUM(E1:E3)"},
                 "after": {"E1": "1", "E2": "4"}, "dsum": {"text": "5", "formula": "=SUM(E1:E2)"}},
    "beyond": {"G1": {"before": {"text": "12", "formula": "=E3*3"}, "after": {"text": "12", "formula": "=E2*3"}},
               "H1": {"before": {"text": "12", "formula": "=$E$3*3"}, "after": {"text": "12", "formula": "=$E$2*3"}},
               "I1": {"before": {"text": "12", "formula": "=E$3*3"}, "after": {"text": "12", "formula": "=E$2*3"}}},
    "samples": [], "visited": ["/"], "entry": {}, "ms": 32536,
}

V145_KEPT = {"afterInsert.A4": {"text": "1", "formula": "=$A$1+1"}, "afterInsert.A5": {"text": "5", "formula": "=A$1+5"}}

def replay(base: dict = CORRECT, **parts) -> dict:
    result = json.loads(json.dumps(base))
    for key, value in parts.items():
        if "." in key:
            part, coord = key.split(".", 1)
            result[part][coord] = value
        else:
            result[key] = value
    return result

def findings(result, said=None, logs=None):
    said = said or statement()
    with Env():
        planned = sp.probe_job(said, None)
    return sp.structure_findings(said, planned, result, log=(logs.append if logs is not None else QUIET))

def kinds(found, prefix):
    return [f for f in found if f["problem"].startswith(prefix)]

def logged(logs, words):
    return any(DIAG in line and words in line for line in logs)

@case
def delete_shift_sentences_per_axis_and_the_copy_rule_is_no_exception():
    said = statement()
    assert said["delete_sentences"]["row"].startswith("on deletion, subsequent rows shift upward"), said["delete_sentences"]
    assert said["delete_sentences"]["column"].startswith("on deletion, subsequent columns shift left"), said["delete_sentences"]
    assert said["absolute_fixed"] == "", said["absolute_fixed"]
    for rule in ("When formulas are copied, relative references adjust while absolute references remain unchanged.",
                 "Pasting a formula keeps $A$1 fixed.", COPY_RULE):
        assert statement(H.SHEET + H.yaml_like(("R", rule)))["absolute_fixed"] == "", rule

@case
def a_fixed_absolute_sentence_outside_copying_is_recognised():
    for rule in ("Absolute references remain unchanged when rows are inserted or deleted.",
                 "Absolute references such as $A$1 never shift.",
                 "References like $A$1 do not move on structural edits.",
                 "Absolute references stay fixed.",
                 "When formulas are copied or rows are inserted, absolute references remain unchanged."):
        got = statement(H.SHEET + H.yaml_like(("R", rule)))["absolute_fixed"]
        assert got and rule in got, (rule, got)

@case
def no_delete_shift_sentence_when_negated_conditioned_or_absent():
    rows_no_delete = H.ROWS_DESC.replace("; on deletion, subsequent rows shift upward and rules on the target row are removed",
                                         "")
    assert rows_no_delete != H.ROWS_DESC
    base = H.yaml_like(("REQ-1-2-1", H.CREATE_DESC), ("REQ-4", H.FORMULA_DESC))
    assert statement(base + H.yaml_like(("R", rows_no_delete)))["delete_sentences"]["row"] == ""
    for clause in ("on deletion, subsequent rows do not shift upward",
                   "when the compact option is enabled, deleting a row shifts subsequent rows upward",
                   "deleting a worksheet shifts the remaining tabs left"):
        text = base + H.yaml_like(("R", rows_no_delete + " " + clause[0].upper() + clause[1:] + "."))
        assert statement(text)["delete_sentences"]["row"] == "", clause

@case
def the_job_plans_the_mixed_cell_and_the_interior_range_on_both_axes():
    planned = job()
    plan = planned["plan"]
    assert (plan["mix"], plan["mix_formula"], plan["i_mix"], plan["i_mix_expect"]) == ("A4", "=A$1+5", "A5", "=A$2+5"), plan
    assert (plan["n_a"], plan["n_b"], plan["n_sum"], plan["n_formula"]) == ("E1", "E2", "E4", "=SUM(E1:E3)"), plan
    assert (plan["d_n_top"], plan["d_n_low"], plan["d_n_sum"], plan["d_n_expect"]) == ("E1", "E2", "E3", "=SUM(E1:E2)"), plan
    assert {"A4", "A5", "E1", "E2", "E3", "E4"} <= set(planned["used"]), planned["used"]
    assert not {"A4", "A5", "E1", "E2", "E3", "E4"} & {s["cell"] for s in planned["samples"]}, planned["samples"]
    other = planned["more"][0]
    assert other["name"] == "other_axis", other
    op = other["plan"]
    assert (op["mix"], op["mix_formula"], op["i_mix"], op["i_mix_expect"]) == ("D1", "=$A1+5", "E1", "=$B1+5"), op
    assert (op["n_a"], op["n_b"], op["n_sum"], op["n_formula"]) == ("A5", "B5", "D5", "=SUM(A5:C5)"), op
    assert (op["d_n_sum"], op["d_n_expect"]) == ("C5", "=SUM(A5:B5)"), op
    assert {"D1", "E1", "A5", "B5", "C5", "D5"} <= set(other["used"]), other["used"]
    json.dumps(planned)
    with Env(ARC_STRUCT_INTERIOR="0"):
        bare = sp.probe_job(statement(), None)
    assert not any(k.startswith(("mix", "i_mix", "n_", "d_n_")) for k in bare["plan"]), bare["plan"]
    assert not {"E1", "E2", "E3", "E4", "A5"} & set(bare["used"]), bare["used"]

@case
def the_probe_script_enters_and_reads_the_new_cells():
    script = sp.PROBE_SCRIPT
    assert "[[P.mix, P.mix_formula], [P.n_a, '2'], [P.n_b, '4']]" in script
    assert "...(P.mix ? [P.mix] : [])" in script and "...(P.mix ? [P.i_mix] : [])" in script
    after_insert = script[script.index("out.interior = { moved"):script.index("await shot('2-after-insert')")]
    assert "enter(P.n_top, '1')" in after_insert and "enter(P.n_sum, P.n_formula)" in after_insert, after_insert
    assert "out.interior.dsum = await read(P.d_n_sum)" in script
    assert "if (P.n_low && !late())" in script
    assert "if (P.n_sum && await enter(P.n_top, '1')" in script
    assert "out.beyond[b.cell] = { before: await read(b.cell) }" in after_insert, after_insert
    assert "out.beyond[b.cell].after = await read(b.cell)" in script

@case
def the_v145_replay_gives_one_absolute_finding_naming_both_cells():
    logs = []
    found = findings(replay(**V145_KEPT), logs=logs)
    assert [f["problem"][:len(ABS)] for f in found] == [ABS], found
    problem = found[0]["problem"]
    for text in ("=$A$1+1", "=$A$2+1", "=A$1+5", "=A$2+5", "“1”", "“5”", "moved correctly to =A2*10"):
        assert text in problem, (text, problem)
    assert "formula references shift downward" in found[0]["requirement"], found[0]["requirement"]
    assert found[0]["where"] == "route /workbooks/2" and "$A$2" in found[0]["fix"], found[0]
    assert not logged(logs, "absolute part"), logs

@case
def a_correct_shift_reports_nothing():
    logs = []
    assert findings(replay(), logs=logs) == [], logs
    assert not logged(logs, "interior") and not logged(logs, "absolute"), logs

@case
def only_the_mixed_cell_kept_is_reported_alone():
    found = kinds(findings(replay(**{"afterInsert.A5": {"text": "5", "formula": "=A$1+5"}})), ABS)
    assert len(found) == 1 and "=A$1+5" in found[0]["problem"] and "=$A$1+1" not in found[0]["problem"], found

@case
def no_absolute_finding_without_a_changed_value_or_with_stripped_markers():
    variants = [
        {"afterInsert.A4": {"text": "3", "formula": "=$A$1+1"}},
        {"afterInsert.A4": {"text": "", "formula": "=$A$1+1"}},
        {"afterInsert.A4": {"text": "1", "formula": "=$A$2+1"}},
        {"afterInsert.A4": {"text": "1", "formula": "=$A$3+1"}},
        {"afterInsert.A5": {"text": "7", "formula": "=A$1+5"}},
        {"afterInsert.A5": {}},
    ]
    for parts in variants:
        assert not kinds(findings(replay(**parts)), ABS), parts
    stripped = findings(replay(**{"afterInsert.A4": {"text": "3", "formula": "=A2+1"},
                                  "afterInsert.A5": {"text": "7", "formula": "=A2+5"}}))
    assert [f["problem"][:len(DOLLAR)] for f in stripped] == [DOLLAR], stripped

@case
def no_absolute_finding_when_the_setup_or_the_insert_is_not_verified():
    for parts in ({"insert": {"error": "command not found"}},
                  {"insert": None},
                  {"afterInsert.A2": {"text": "", "formula": ""}},
                  {"afterInsert.old": {"text": "2"}},
                  {"setup.A2": {"text": "20", "formula": "20"}}):
        found = findings(replay(**dict(V145_KEPT, **parts)))
        assert not kinds(found, ABS), (parts, found)

    for setup in ({"text": "7", "formula": "=A1+5"}, {"text": "", "formula": ""}, {"text": "0", "formula": "=A$1+5"}):
        found = kinds(findings(replay(**dict(V145_KEPT, **{"setup.A4": setup}))), ABS)
        assert len(found) == 1 and "=$A$1+1" in found[0]["problem"] and "=A$1+5" not in found[0]["problem"], (setup, found)

@case
def a_fixed_absolute_sentence_logs_instead_of_reporting():
    said = statement(SHEET + H.yaml_like(("R", "Absolute references stay fixed when rows are inserted.")))
    logs = []
    found = findings(replay(**V145_KEPT), said=said, logs=logs)
    assert not kinds(found, ABS), found
    assert logged(logs, "absolute part not moved"), logs

@case
def an_old_result_without_the_new_cells_is_judged_as_before():
    logs = []
    found = findings(H.P141, logs=logs)
    assert [f["problem"][:len(DOLLAR)] for f in found] == [DOLLAR], found
    assert not any(DIAG in line for line in logs), logs
    old_job = H.job()
    plain = {k: v for k, v in statement().items() if k not in ("delete_sentences", "absolute_fixed")}
    assert len(sp.structure_findings(plain, old_job, replay(**V145_KEPT), log=QUIET)) == 1

@case
def a_broken_interior_range_is_reported():
    for dsum in ({"text": "#REF!", "formula": "=SUM(E1:E3)"},
                 {"text": "#REF!", "formula": "=#REF!"},
                 {"text": "9", "formula": "=SUM(E1:E3)"}):
        found = findings(replay(**{"interior.dsum": dsum}))
        assert [f["problem"][:len(INTERIOR)] for f in found] == [INTERIOR], (dsum, found)
        problem = found[0]["problem"]
        assert dsum["formula"] in problem and "=SUM(E1:E2) = 5" in problem and "“Delete row” on row 2" in problem, problem
        assert "on deletion, subsequent rows shift upward" in found[0]["requirement"], found[0]["requirement"]

@case
def an_interior_finding_needs_the_delete_shift_sentence():
    said = dict(statement(), delete_sentences={"row": "", "column": "x"})
    logs = []
    assert findings(replay(**{"interior.dsum": {"text": "#REF!", "formula": "=#REF!"}}), said=said, logs=logs) == []
    assert logged(logs, "no sentence says rows shift on deletion"), logs

@case
def an_unverified_interior_step_reports_nothing():
    broken = {"text": "#REF!", "formula": "=#REF!"}
    variants = [
        {"interior.moved": {"E1": "", "E2": "", "E3": "4"}},
        {"interior.moved": {"E1": "9", "E2": "2", "E3": "4"}},
        {"interior.sum": {"text": "0", "formula": "=SUM(E1:E3)"}},
        {"interior.sum": {"text": "7", "formula": "7"}},
        {"interior.after": {"E1": "1", "E2": "2"}},
        {"interior.after": {}},
        {"interior.dsum": {"text": "", "formula": ""}},
        {"interior.dsum": {"text": "#REF!", "formula": "7"}},
        {"del": {"error": "command not found"}},
        {"afterDelete.old": {"text": "2"}},
        {"interior": {"moved": {"E1": "", "E2": "2", "E3": "4"}}},
        {"interior": "garbage"},
    ]
    assert kinds(findings(replay(**{"interior.dsum": broken})), INTERIOR)
    for parts in variants:
        merged = {"interior.dsum": broken, **parts}
        found = findings(replay(**merged))
        assert not kinds(found, INTERIOR), (parts, found)

    logs = []
    findings(replay(**{"interior.dsum": broken, "interior.after": {"E1": "1", "E2": "2"}}), logs=logs)
    assert logged(logs, "interior range not verified"), logs

@case
def a_shrunk_range_or_the_right_sum_under_another_text_is_quiet():
    assert findings(replay()) == []
    logs = []
    assert findings(replay(**{"interior.dsum": {"text": "5", "formula": "=SUM(E1:E2,E9)"}}), logs=logs) == []
    assert logged(logs, "interior range"), logs

@case
def the_column_axis_is_judged_with_its_own_cells():
    said = statement()
    with Env():
        planned = sp.probe_job(said, None)
    sub = planned["more"][0]
    col = {"axis": "column", "route": "/workbooks/3", "created": "New blank workbook", "bar": True,
           "setup": {"A1": {"text": "2", "formula": "2"}, "B1": {"text": "20", "formula": "=A1*10"},
                     "C1": {"text": "3", "formula": "=$A$1+1"}, "C3": {"text": "12", "formula": "=SUM(A3:B3)"},
                     "D1": {"text": "7", "formula": "=$A1+5"}},
           "insert": {"how": "right-click"},
           "afterInsert": {"old": {"text": ""}, "B1": {"text": "2", "formula": "2"}, "C1": {"text": "20", "formula": "=B1*10"},
                           "D1": {"text": "1", "formula": "=$A$1+1"}, "E1": {"text": "5", "formula": "=$A1+5"}},
           "del": {"how": "right-click"},
           "afterDelete": {"old": {"text": ""}, "B1": {"text": "#REF!", "formula": "=#REF!*10"}},
           "interior": {"moved": {"A5": "", "B5": "2", "C5": "4"}, "sum": {"text": "7", "formula": "=SUM(A5:C5)"},
                        "after": {"A5": "1", "B5": "4"}, "dsum": {"text": "#REF!", "formula": "=#REF!"}},
           "samples": []}
    result = replay(**{"more": {"other_axis": col}})
    found = sp.structure_findings(said, planned, result, log=QUIET)
    names = [f["problem"][:40] for f in found]
    assert len(found) == 2, names
    assert found[0]["problem"].startswith("Absolute reference not moved by a column insert"), found[0]
    assert "=$A1+5" in found[0]["problem"] and "=$B1+5" in found[0]["problem"], found[0]["problem"]
    assert found[1]["problem"].startswith("Range broken by deleting a column") and "=SUM(A5:B5)" in found[1]["problem"], found[1]
    assert sub["plan"]["d_n_sum"] == "C5"

V145_DELETE = {"beyond.H1": {"before": {"text": "12", "formula": "=$E$3*3"}, "after": {"text": "15", "formula": "=$E$3*3"}},
               "beyond.I1": {"before": {"text": "12", "formula": "=E$3*3"}, "after": {"text": "15", "formula": "=E$3*3"}}}
POLICY = "Any range touching a deleted cell becomes invalid and shows #REF!."

@case
def references_beyond_the_deleted_line_are_planned_on_both_axes():
    planned = job()
    assert [(b["kind"], b["cell"], b["formula"], b["expect"]) for b in planned["plan"]["beyond"]] == [
        ("relative", "G1", "=E3*3", "=E2*3"), ("absolute", "H1", "=$E$3*3", "=$E$2*3"),
        ("mixed", "I1", "=E$3*3", "=E$2*3")], planned["plan"]["beyond"]
    other = planned["more"][0]["plan"]["beyond"]
    assert [(b["cell"], b["formula"], b["expect"]) for b in other] == [
        ("A7", "=C5*3", "=B5*3"), ("A8", "=$C$5*3", "=$B$5*3"), ("A9", "=$C5*3", "=$B5*3")], other

@case
def kept_dollar_parts_on_delete_join_the_insert_finding():
    found = findings(replay(**V145_KEPT, **V145_DELETE))
    assert [f["problem"][:len(ABS)] for f in found] == [ABS], found
    problem = found[0]["problem"]
    assert problem.startswith(ABS + " row insert and delete:"), problem
    for text in ("=$A$1+1", "=$E$3*3", "=E$3*3", "“15”", "=$E$2*3", "=E2*3 = 12", "from E3 to E2"):
        assert text in problem, (text, problem)
    only = findings(replay(**V145_DELETE))
    assert [f["problem"][:len(ABS) + 11] for f in only] == [ABS + " row delete"], only
    assert "=$A$1+1" not in only[0]["problem"], only

@case
def the_delete_half_needs_its_contrast_and_verified_steps():
    for parts in ({"beyond.G1": {"before": {"text": "12", "formula": "=E3*3"}, "after": {"text": "15", "formula": "=E3*3"}}},
                  {"interior.after": {"E1": "1", "E2": "2"}},
                  {"interior.moved": {"E1": "", "E2": "", "E3": "4"}},
                  {"afterDelete.old": {"text": "2"}},
                  {"del": {"error": "command not found"}},
                  {"beyond.H1": {"before": {"text": "0", "formula": "=$E$3*3"}, "after": {"text": "15", "formula": "=$E$3*3"}}},
                  {"beyond.G1": {"before": {"text": "12", "formula": "=E3*3"}}},
                  {"beyond": {}}, {"beyond": "garbage"}):
        merged = {**V145_DELETE, **parts}
        found = findings(replay(**merged))
        assert not kinds(found, ABS), (parts, found)
    logs = []
    findings(replay(**{**V145_DELETE, "beyond.G1": {"before": {"text": "12", "formula": "=E3*3"},
                                                   "after": {"text": "15", "formula": "=E3*3"}}}), logs=logs)
    assert logged(logs, "references beyond the deleted line not judged"), logs

    for after in ({"text": "12", "formula": "=E2*3"}, {"text": "12", "formula": "=$E$3*3"}, {"text": "", "formula": "=$E$3*3"}):
        assert not kinds(findings(replay(**{"beyond.H1": {"before": {"text": "12", "formula": "=$E$3*3"}, "after": after}})), ABS), after
    said = statement(SHEET + H.yaml_like(("R", "Absolute references stay fixed when rows are deleted.")))
    logs = []
    assert not kinds(findings(replay(**V145_DELETE), said=said, logs=logs), ABS)
    assert logged(logs, "absolute part not moved"), logs

@case
def the_insert_half_now_needs_the_relative_contrast():
    unshifted = {"afterInsert.A3": {"text": "0", "formula": "=A1*10"}}
    found = findings(replay(**V145_KEPT, **unshifted))
    assert not kinds(found, ABS) and kinds(found, "References not shifted on insert"), found

@case
def a_stated_range_policy_switches_the_interior_check_off():
    said = statement(SHEET + H.yaml_like(("R", POLICY)))
    assert POLICY in said["range_invalid"], said["range_invalid"]
    for text in (H.SHEET, SHEET, H.SHEET + H.yaml_like(("R", "Deleting a rule removes its range from the dialog."))):
        assert statement(text)["range_invalid"] == "", text[-80:]
    with Env():
        plan = sp.probe_job(said, None)["plan"]
    assert "n_sum" not in plan and "d_n_sum" not in plan and plan["beyond"] and plan["n_low"] == "E3", plan
    logs = []
    broken = {"interior.dsum": {"text": "#REF!", "formula": "=#REF!"}}
    assert findings(replay(**broken), said=said, logs=logs) == [], logs
    assert logged(logs, "interior range not judged"), logs

    no_sum = {**V145_DELETE, "interior": {"moved": {"E1": "", "E2": "2", "E3": "4"}, "after": {"E1": "", "E2": "4"}}}
    assert kinds(findings(replay(**no_sum), said=said), ABS)

class _Server:

    def __init__(self, alive, output=""):
        self.answers, self.output, self.stopped = list(alive), output, 0

    def __call__(self, *args, **kwargs):
        return self

    def alive(self):
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]

    def output_so_far(self):
        return self.output

    def stop(self):
        self.stopped += 1
        return self.output

def _probe_with(server, clock=None, answers_before=False, results=None):
    import tempfile
    import time
    from agent import fsutil
    names = ("BackgroundProcess", "free_port", "fresh_data_env", "npm_bin", "wait_http")
    saved = {n: getattr(fsutil, n) for n in names}
    saved_run, saved_answers, saved_clock = sp.run_probe, sp._answers, time.monotonic
    calls, logs = [], []

    def fake_run(backend, base, sub, log=print):
        calls.append(sub)
        return (results or {}).get(sub.get("name"), {"route": "/r", "who": sub.get("name") or "main"})

    tmp = Path(tempfile.mkdtemp(prefix="v149e-"))
    try:
        (tmp / "backend" / "node_modules" / "@playwright" / "test").mkdir(parents=True)
        fsutil.BackgroundProcess, fsutil.free_port = server, (lambda preferred=3000: 3999)
        fsutil.fresh_data_env, fsutil.npm_bin = (lambda project_dir: {}), (lambda name: name)
        fsutil.wait_http = lambda url, timeout=60: (True, 200)
        sp.run_probe, sp._answers = fake_run, (lambda url: answers_before)
        if clock is not None:
            time.monotonic = clock
        with Env():
            planned = job()

        planned["more"] = [dict(planned["more"][0], name=name) for name in ("other_axis", "sort", "pivot")]
        return sp.probe(tmp, planned, log=logs.append), calls, logs
    finally:
        for n, v in saved.items():
            setattr(fsutil, n, v)
        sp.run_probe, sp._answers, time.monotonic = saved_run, saved_answers, saved_clock
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

@case
def a_further_run_starts_only_when_its_worst_case_still_fits():
    assert sp.FOLLOW_TIMEOUT_S * 1000 > sp.FOLLOW_BUDGET_MS and sp.FOLLOW_TIMEOUT_S < sp.MORE_BUDGET_S
    result, calls, logs = _probe_with(_Server([True]))
    assert [c.get("name") for c in calls] == [None, "other_axis", "sort", "pivot"], calls
    assert "_timeout_s" not in calls[0] and "_budget_ms" not in calls[0], calls[0].keys()
    for sub in calls[1:]:
        assert sub["_timeout_s"] == sp.FOLLOW_TIMEOUT_S and sub["_budget_ms"] == sp.FOLLOW_BUDGET_MS, sub.keys()

    ticks = iter([0.0, 0.0, 89.0, 91.0, 91.0, 91.0])
    result, calls, logs = _probe_with(_Server([True]), clock=lambda: next(ticks))
    assert [c.get("name") for c in calls] == [None, "other_axis", "sort"], [c.get("name") for c in calls]
    assert any("pivot skipped (time budget)" in line for line in logs), logs

    ticks = iter([0.0, 149.0, 149.0, 149.0, 149.0])
    result, calls, logs = _probe_with(_Server([True]), clock=lambda: next(ticks))
    assert [c.get("name") for c in calls] == [None], calls

    import inspect
    source = inspect.getsource(sp.run_probe)
    assert 'job.get("_timeout_s") or RUN_TIMEOUT_S' in source and 'job.get("_budget_ms") or BUDGET_MS' in source, source

@case
def no_result_unless_the_answering_server_is_the_one_started():
    ok, calls, _ = _probe_with(_Server([True]))
    assert ok and ok["who"] == "main" and len(calls) == 4, ok
    for server, words in ((_Server([False]), "is not running"),
                          (_Server([True], "Error: listen EADDRINUSE: address already in use :::3999"), "EADDRINUSE"),
                          (_Server([True, False]), "after the runs")):
        result, calls, logs = _probe_with(server)
        assert result is None, (words, result)
        assert any(words in line for line in logs), (words, logs)
        assert server.stopped == 1, words
    result, calls, logs = _probe_with(_Server([True]), answers_before=True)
    assert result is None and calls == [] and any("answers before the start" in line for line in logs), logs
    assert sp._answers("http://127.0.0.1:9/") is False

DELETE_SENTENCE = " On deletion, subsequent rows shift upward."
FIXED_SENTENCE = " Absolute references stay fixed when rows are inserted."

def _page() -> str:
    page = G.PAGE.replace("const COLS = 'ABCDEF'", "const COLS = 'ABCDEFGHI'").replace("[A-F]", "[A-I]")
    assert "'ABCDEFGHI'" in page and "[A-F]" not in page, "the v146d page changed: adapt _page"
    old = "    next[k] = v.replace(REF, (m, a, c, b, r) => {\n      r = +r;\n"
    new = ("    let w = v;\n"
           "    if (MODE.includes('interior') && by < 0) w = w.replace(/([A-I])(\\d+):([A-I])(\\d+)/g,\n"
           "      (m, c1, r1, c2, r2) => (+r1 < at && +r2 > at) ? '#REF!' : m);\n"
           "    next[k] = w.replace(REF, (m, a, c, b, r) => {\n"
           "      if (MODE.includes('keepabs') && b) return m;\n      r = +r;\n")
    assert page.count(old) == 1, "the v146d page changed: adapt _page"
    return page.replace(old, new)

def _live(mode: str, req: str):
    saved = G.PAGE, G.REQ
    try:
        G.PAGE, G.REQ = _page(), req
        with Env():
            return G._live(mode)
    finally:
        G.PAGE, G.REQ = saved

@case
def live_a_correct_shifter_gives_no_finding():
    result, found, lines = _live("readonly", G.REQ + DELETE_SENTENCE)
    assert result["afterInsert"]["A4"]["formula"] == "=$A$2+1" and result["afterInsert"]["A5"]["formula"] == "=A$2+5", result
    assert result["interior"]["dsum"] == {"text": "5", "formula": "=SUM(E1:E2)"}, result["interior"]
    assert {c: v["after"]["formula"] for c, v in result["beyond"].items()} == {"G1": "=E2*3", "H1": "=$E$2*3", "I1": "=E$2*3"}, result["beyond"]
    assert found == [], found

@case
def live_kept_dollar_parts_give_the_absolute_finding_only():
    result, found, lines = _live("readonly-keepabs", G.REQ + DELETE_SENTENCE)
    assert result["afterInsert"]["A4"] == {"text": "1", "formula": "=$A$1+1"}, result["afterInsert"]
    assert result["beyond"]["H1"]["after"]["formula"] == "=$E$3*3", result["beyond"]
    assert [f["problem"][:len(ABS)] for f in found] == [ABS], found
    assert found[0]["problem"].startswith(ABS + " row insert and delete"), found[0]["problem"]
    for text in ("=A$1+5", "=$E$3*3", "=E$3*3", "=$E$2*3"):
        assert text in found[0]["problem"], (text, found[0]["problem"])
    _, quiet, lines = _live("readonly-keepabs", G.REQ + DELETE_SENTENCE + FIXED_SENTENCE)
    assert quiet == [] and logged(lines, "absolute part not moved"), (quiet, lines)

@case
def live_a_broken_interior_range_gives_the_interior_finding_only():
    result, found, lines = _live("readonly-interior", G.REQ + DELETE_SENTENCE)
    assert result["interior"]["dsum"]["text"] == "#REF!", result["interior"]
    assert [f["problem"][:len(INTERIOR)] for f in found] == [INTERIOR], found
    _, quiet, lines = _live("readonly-interior", G.REQ)
    assert quiet == [] and logged(lines, "no sentence says rows shift on deletion"), (quiet, lines)

def main() -> int:
    if not __debug__:
        print("FAIL  self-tests need assertions: run without -O and without PYTHONOPTIMIZE")
        return 1
    failed = skipped = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except unittest.SkipTest as skip:
            skipped += 1
            print(f"SKIP  {function.__name__}: {skip}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=6)
    print(f"{len(CASES) - failed - skipped}/{len(CASES)} v149e cases passed ({skipped} skipped)")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
