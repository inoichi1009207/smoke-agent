from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

NL = chr(10)
CASES: list = []
SKIPPED: list[str] = []

def case(function):
    CASES.append(function)
    return function

PRELUDE = """
class El {
  constructor(spec, parent) {
    this.tagName = String(spec.tag || 'div').toUpperCase();
    this.attrs = spec.attrs || {};
    this.rect = spec.rect || [0, 0, 0, 0];
    this.style = spec.style || {};
    this.text = spec.text;
    this.parentElement = parent || null;
    this.kids = (spec.kids || []).map((k) => new El(k, this));
  }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) ? String(this.attrs[name]) : null; }
  getBoundingClientRect() {
    const [left, top, width, height] = this.rect;
    return { left, top, width, height, right: left + width, bottom: top + height, x: left, y: top };
  }
  matchesOne(sel) {
    if (sel.startsWith('[role=') && sel.endsWith(']')) return this.attrs.role === sel.slice(6, -1);
    if (sel.endsWith(':not([role])')) return this.tagName.toLowerCase() === sel.slice(0, -12) && !('role' in this.attrs);
    throw new Error('unsupported selector ' + sel);
  }
  matches(sel) { return sel.split(',').some((s) => this.matchesOne(s.trim())); }
  closest(sel) { let e = this; while (e) { if (e.matches(sel)) return e; e = e.parentElement; } return null; }
  querySelectorAll(sel) {
    const out = [];
    const walk = (e) => { for (const k of e.kids) { if (k.matches(sel)) out.push(k); walk(k); } };
    walk(this);
    return out;
  }
  get innerText() { return this.text !== undefined ? String(this.text) : this.kids.map((k) => k.innerText).join(String.fromCharCode(10)); }
}
globalThis.getComputedStyle = (el) => ({ visibility: el.style.visibility || 'visible', display: el.style.display || 'block',
  position: el.style.position || 'static' });
"""

def _js_block(name: str, end_marker: str) -> str:
    from agent import audit
    script = audit.SNAPSHOT_SCRIPT
    start = script.index(f"const {name} = ")
    return script[start:script.index(end_marker, start)]

def run_row_check(pages: list[dict]) -> list[dict]:
    node = shutil.which("node")
    assert node, "node is needed for the ROW_CHECK cases"
    body = _js_block("ROW_CHECK", NL + "// like GRID_TWICE")
    probe = NL.join([
        PRELUDE, body,
        "const fs = require('fs');",
        "const pages = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));",
        "const out = [];",
        "for (const page of pages) {",
        "  globalThis.document = new El({ tag: 'body', rect: [0, 0, 1280, 800], kids: page });",
        "  const r = ROW_CHECK();",
        "  delete r.ms;",
        "  out.push(r);",
        "}",
        "console.log(JSON.stringify(out));",
    ])
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "rows.cjs"
        data = Path(tmp) / "pages.json"
        script.write_text(probe, encoding="utf-8")
        data.write_text(json.dumps(pages), encoding="utf-8")
        done = subprocess.run([node, str(script), str(data)], capture_output=True, text=True, timeout=60)
        assert done.returncode == 0, done.stderr[-800:]
        return json.loads(done.stdout)

def cell(text: str, left: float, top: float, width: float = 96, height: float = 29, role: str = "gridcell", **style) -> dict:
    return {"tag": "div", "attrs": {"role": role}, "rect": [left, top, width, height], "text": text, "style": style}

def row(cells: list[dict], role: str | None = "row", tag: str = "div") -> dict:
    left = min((c["rect"][0] for c in cells), default=0)
    top = min((c["rect"][1] for c in cells), default=0)
    right = max((c["rect"][0] + c["rect"][2] for c in cells), default=0)
    bottom = max((c["rect"][1] + c["rect"][3] for c in cells), default=0)
    return {"tag": tag, "attrs": {"role": role} if role else {}, "rect": [left, top, right - left, bottom - top], "kids": cells}

def grid(rows: list[dict], label: str = "Worksheet grid", role: str | None = "grid", tag: str = "div") -> dict:
    attrs = ({"role": role} if role else {}) | ({"aria-label": label} if label else {})
    return {"tag": tag, "attrs": attrs, "rect": [0, 100, 1280, 700], "kids": rows}

COLUMNS = "ABCDEFGHIJKL"

def data_rows(first_top: float, count: int = 4, x0: float = 25) -> list[dict]:
    out = []
    for r in range(count):
        top = first_top + 29 * r
        cells = [cell(str(r + 1), x0, top, 40, role="rowheader")] + [cell("", x0 + 40 + 96 * i, top) for i in range(len(COLUMNS))]
        out.append(row(cells))
    return out

def sheet_v141_page() -> list[dict]:
    header = [cell("", 25, 190, 64, 9, role="columnheader")] + [cell(c, 25, 199 + 29 * i, role="columnheader") for i, c in enumerate(COLUMNS)]
    return [grid([row(header)] + data_rows(547))]

def correct_grid_page() -> list[dict]:
    header = [cell("", 25, 190, 40, role="columnheader")] + [cell(c, 65 + 96 * i, 190, role="columnheader") for i, c in enumerate(COLUMNS)]
    return [grid([row(header)] + data_rows(219))]

@case
def the_sheet_v141_header_row_is_found_and_a_correct_grid_is_not():
    v141, correct = run_row_check([sheet_v141_page(), correct_grid_page()])
    assert v141["stackedRows"] == 1 and v141["rowsChecked"] == 5, v141
    [found] = v141["found"]
    assert found["container"] == "Worksheet grid" and found["cells"] == 13, found
    assert found["row"].split(" | ")[1:3] == ["A", "B"], found
    assert found["boxes"][:2] == [[25, 190, 64, 9], [25, 199, 96, 29]], found
    assert correct == {"rowsChecked": 5, "stackedRows": 0, "found": []}, correct

@case
def a_single_cell_row_and_a_page_without_rows_are_quiet():
    single = [grid([row([cell("Only", 25, 200)]), row([cell("One", 25, 229)])], label="Names")]
    no_rows = [{"tag": "main", "rect": [0, 0, 1280, 800], "kids": [
        {"tag": "ul", "attrs": {}, "rect": [0, 100, 600, 200], "kids": [
            {"tag": "li", "attrs": {}, "rect": [0, 100, 600, 30], "text": "acme/docs"},
            {"tag": "li", "attrs": {}, "rect": [0, 130, 600, 30], "text": "acme/site"}]}]}]
    for result in run_row_check([single, no_rows]):
        assert result == {"rowsChecked": 0, "stackedRows": 0, "found": []}, result

@case
def only_the_all_stacked_same_column_shape_counts():
    wrapped = row([cell("a", 0, 200), cell("b", 96, 200), cell("c", 0, 229), cell("d", 96, 229)])
    staircase = row([cell("a", 0, 200), cell("b", 96, 229), cell("c", 192, 258)])
    overlaid = row([cell("a", 0, 200), cell("b", 0, 200)])
    half_overlap_less = row([cell("a", 0, 200, 100), cell("b", 60, 229, 100)])
    half_overlap = row([cell("a", 0, 200, 100), cell("b", 50, 229, 100)])
    slack = row([cell("a", 0, 200), cell("b", 0, 227)])
    into = row([cell("a", 0, 200), cell("b", 0, 225)])
    label_on_top = row([cell("Region", 0, 200), cell("West", 0, 229), cell("500", 96, 229)])
    pages = [[grid([r], label=f"g{i}")] for i, r in enumerate([wrapped, staircase, overlaid, half_overlap_less, half_overlap, slack, into,
                                                               label_on_top])]
    results = run_row_check(pages)
    assert [r["stackedRows"] for r in results] == [0, 0, 0, 0, 1, 1, 0, 0], results
    assert all(r["rowsChecked"] == 1 for r in results), results

@case
def hidden_and_zero_size_cells_and_nested_rows_do_not_make_a_stack():
    hidden = row([cell("a", 0, 200), cell("b", 0, 229, visibility="hidden")])
    zero = row([cell("a", 0, 200), cell("", 0, 229, 96, 0)])
    fixed = row([cell("a", 0, 200), cell("tip", 0, 229, position="fixed")])

    tree = {"tag": "div", "attrs": {"role": "row"}, "rect": [0, 200, 300, 87], "kids": [
        cell("Folder", 0, 200, role="gridcell"),
        {"tag": "div", "attrs": {"role": "group"}, "rect": [0, 229, 300, 58], "kids": [row([cell("child 1", 0, 229)]), row([cell("child 2", 0, 258)])]}]}
    results = run_row_check([[grid([r], label="t")] for r in (hidden, zero, fixed, tree)])
    for result in results:
        assert result == {"rowsChecked": 0, "stackedRows": 0, "found": []}, results

@case
def a_plain_table_row_counts_like_a_role_row_and_at_most_five_are_listed():
    td = lambda text, left, top: {"tag": "td", "attrs": {}, "rect": [left, top, 96, 29], "text": text}
    table = grid([row([td("x", 0, 200 + 58 * i), td("y", 0, 229 + 58 * i)], role=None, tag="tr") for i in range(7)],
                 label="", role=None, tag="table")
    [result] = run_row_check([[table]])
    assert result["stackedRows"] == 7 and result["rowsChecked"] == 7 and len(result["found"]) == 5, result
    assert result["found"][0]["container"] == "table" and result["found"][0]["row"] == "x | y", result

@case
def a_stacked_row_counts_only_when_the_second_look_finds_it_again():
    node = shutil.which("node")
    assert node, "node is needed"
    twice = _js_block("ROW_TWICE", NL + "const HAS_GRID")
    probe = NL.join([
        "const ROW_CHECK = 'check';",
        twice,
        "const f = (container, row, cells) => ({ container, row, cells });",
        "const fake = (answers) => { const waits = []; const page = {",
        "  evaluate: async () => answers.shift(), waitForTimeout: async (ms) => { waits.push(ms); } }; return { page, waits }; };",
        "(async () => {",
        "  const out = [];",
        "  let t = fake([{ ms: 1, rowsChecked: 5, stackedRows: 2, found: [f('g', 'A | B', 13), f('g', 'x', 2)] },",
        "                { ms: 2, rowsChecked: 5, stackedRows: 1, found: [f('g', 'A | B', 13)] }]);",
        "  let r = await ROW_TWICE(t.page); out.push([r.found.map((x) => x.row), t.waits, r.measured, r.stackedRows]);",
        "  t = fake([{ ms: 1, rowsChecked: 5, stackedRows: 1, found: [f('g', 'A | B', 13)] }, { ms: 1, rowsChecked: 5, stackedRows: 0, found: [] }]);",
        "  r = await ROW_TWICE(t.page); out.push([r.found.length, t.waits]);",
        "  t = fake([{ ms: 1, rowsChecked: 5, stackedRows: 0, found: [] }, { ms: 1, rowsChecked: 5, stackedRows: 1, found: [f('g', 'A', 2)] }]);",
        "  r = await ROW_TWICE(t.page); out.push([r.found.length, t.waits]);",
        "  t = fake([{ ms: 1, rowsChecked: 5, stackedRows: 1, found: [f('g', 'A | B', 13)] }, { ms: 1, rowsChecked: 5, stackedRows: 1, found: [f('g', 'A | B', 12)] }]);",
        "  r = await ROW_TWICE(t.page); out.push([r.found.length]);",
        "  console.log(JSON.stringify(out));",
        "})();",
    ])
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "twice.cjs"
        path.write_text(probe, encoding="utf-8")
        done = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=60)
        assert done.returncode == 0, done.stderr[-500:]
        assert json.loads(done.stdout) == [[["A | B"], [600], 2, 1], [0, [600]], [0, []], [0]], done.stdout

@case
def the_snapshot_measures_rows_on_every_page_kind_and_keeps_a_page_with_a_stacked_row():
    from agent import audit
    script = audit.SNAPSHOT_SCRIPT
    body = _js_block("ROW_CHECK", NL + "// like GRID_TWICE")
    assert "\\" not in body and "\\" not in _js_block("ROW_TWICE", NL + "const HAS_GRID")
    assert script.count("entry.rowLayout = await ROW_TWICE(page);") == 3
    assert "else if (entry.rowLayout && entry.rowLayout.found.length) out.push(entry);" in script
    assert "else if (entry.via && entry.rowLayout && entry.rowLayout.found.length) out.push(entry);" in script

def _page(route: str, found: list[dict], stacked: int | None = None, grid_found: list[dict] | None = None, via: str | None = None) -> dict:
    page = {"route": route, "rowLayout": {"rowsChecked": 51, "stackedRows": len(found) if stacked is None else stacked, "found": found},
            "gridLayout": {"seen": [], "found": grid_found or []}}
    if via:
        page["via"] = via
    return page

V141_FOUND = {"container": "Worksheet grid", "cells": 13, "row": " | A | B | C", "boxes": [[25, 190, 64, 9], [25, 199, 96, 29], [25, 228, 96, 29]]}

@case
def the_v141_snapshot_gives_one_finding_and_github_gives_none():
    from agent.audit import row_layout_findings
    v141 = [{"route": "/", "rowLayout": {"rowsChecked": 0, "stackedRows": 0, "found": []}, "gridLayout": {"found": []}},
            _page("/workbooks/1", [V141_FOUND])]
    [finding] = row_layout_findings(v141)
    assert finding["where"] == "route /workbooks/1", finding
    assert "stacked vertically: 1 row(s)" in finding["problem"] and "'Worksheet grid' with 13 cells '| A | B | C'" in finding["problem"], finding
    assert "display: flex" in finding["fix"], finding
    github = [{"route": f"/r{i}", "rowLayout": {"rowsChecked": 0, "stackedRows": 0, "found": []}} for i in range(31)]
    assert row_layout_findings(github) == [] and row_layout_findings(None) == [] and row_layout_findings([None, "x", {}]) == []

@case
def a_grid_that_grid_layout_reports_is_left_to_it():
    from agent.audit import row_layout_findings
    rows = [{"container": "Worksheet grid", "cells": 11, "row": f"{i} | West | 500", "boxes": []} for i in range(5)]

    v112 = _page("/workbooks/1", rows, stacked=21, grid_found=[{"grid": "Worksheet grid", "rowsChecked": 21, "stackedRows": 21}])
    assert row_layout_findings([v112]) == []

    half = _page("/workbooks/1", rows[:1], stacked=1, grid_found=[{"grid": "Worksheet grid", "rowsChecked": 2, "stackedRows": 1}])
    [finding] = row_layout_findings([half])
    assert "1 row(s)" in finding["problem"], finding

    other = _page("/workbooks/1", rows + [{"container": "Pivot table", "cells": 3, "row": "Region | Sum", "boxes": []}], stacked=22,
                  grid_found=[{"grid": "Worksheet grid", "rowsChecked": 21, "stackedRows": 21}])
    [finding] = row_layout_findings([other])
    assert "'Pivot table' with 3 cells" in finding["problem"] and "1 row(s)" in finding["problem"], finding

@case
def one_finding_a_page_three_pages_at_most_and_the_click_is_named():
    from agent.audit import MAX_ROW_LAYOUT_FINDINGS, row_layout_findings
    pages = [_page(f"/p{i}", [V141_FOUND, dict(V141_FOUND, row="1 | 2")]) for i in range(5)]
    found = row_layout_findings(pages)
    assert MAX_ROW_LAYOUT_FINDINGS == 3 and [f["where"] for f in found] == ["route /p0", "route /p1", "route /p2"], found
    assert "2 row(s)" in found[0]["problem"], found[0]
    [clicked] = row_layout_findings([_page("/workbooks/1", [V141_FOUND], via="Budget")])
    assert "(reached by clicking “Budget” on /)" in clicked["problem"], clicked
    assert row_layout_findings([_page("/x", [dict(V141_FOUND, cells=1)])]) == []

@case
def audit_node_sends_the_stacked_row_as_a_scripted_finding():
    from agent import audit
    saved = (audit.take_snapshot, audit.deterministic_findings)
    try:
        audit.take_snapshot = lambda *a, **k: [_page("/workbooks/1", [V141_FOUND])]
        audit.deterministic_findings = lambda *a, **k: []
        logs: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            found = audit.audit_node(None, Path(tmp), {"id": "REQ-3-1-1", "members": ["REQ-3-1-1"]}, "{}", log=logs.append, use_llm=False)
        [finding] = found
        assert finding["layer"] == "scripted" and "stacked vertically" in finding["problem"], found
        assert any("row whose cells are stacked vertically" in line for line in logs), logs
    finally:
        audit.take_snapshot, audit.deterministic_findings = saved

@case
def alternative_actions_of_the_subject_become_one_statement_each():
    from agent.coverage import alternatives
    assert alternatives("Pressing Enter or clicking another cell commits the change") == [
        "Pressing Enter commits the change", "Clicking another cell commits the change"]
    assert alternatives("If a selected header is deleted, refreshing or opening the pivot table editor displays a visible error") == [
        "If a selected header is deleted, refreshing displays a visible error",
        "If a selected header is deleted, opening the pivot table editor displays a visible error"]
    assert alternatives("closing or reopening must not update any branch.") == [
        "Closing must not update any branch.", "Reopening must not update any branch."]
    assert alternatives("Refreshing, reopening, or restarting the app shows the saved name") == [
        "Refreshing shows the saved name", "Reopening shows the saved name", "Restarting the app shows the saved name"]

@case
def other_uses_of_or_stay_one_statement():
    from agent.coverage import alternatives
    for clause in (
        "pressing Enter or activating a separate search button is not required before selecting an option.",
        "Selecting another cell or range updates the ARIA state",
        "Press Enter or Tab to commit the change",
        "The session remains valid after refreshing or reopening the workspace",
        "Selecting Public and activating Confirm visibility completes the change",
        "Filtering does not create or modify work items.",
        "Pressing A, pressing B, pressing C, pressing D or pressing E saves the cell",
    ):
        assert alternatives(clause) == [clause], (clause, alternatives(clause))

@case
def a_split_never_pushes_a_statement_out_or_repeats_one():
    from agent import coverage

    both = coverage.statements_of("Pressing Enter commits the change and shows the value. Pressing Enter or clicking another "
                                  "cell commits the change and shows the value.")
    texts = [s.text for s in both]
    assert texts == ["Pressing Enter commits the change and shows the value.",
                     "Clicking another cell commits the change and shows the value."], texts

    filler = " ".join(f"The page shows the message “Item {i} saved”." for i in range(coverage.CAP - 1))
    full = coverage.statements_of(filler + " Refreshing or reopening shows the most recently saved name.")
    assert len(full) == coverage.CAP and full[-1].text == "Refreshing or reopening shows the most recently saved name.", full[-1]

    fewer = " ".join(f"The page shows the message “Item {i} saved”." for i in range(coverage.CAP - 2))
    split = coverage.statements_of(fewer + " Refreshing or reopening shows the most recently saved name.")
    assert [s.text for s in split[-2:]] == ["Refreshing shows the most recently saved name.", "Reopening shows the most recently saved name."], split[-2:]
    assert split[-1].score == split[-2].score and split[-1].position == split[-2].position

def census(*names: str):
    from cases_boilerplate import find_samples
    samples = find_samples()

    def register(function):
        missing = [name for name in names if not samples or not (samples / name).is_file()]
        if missing and str(os.environ.get("ARC_REQUIRE_SAMPLES") or "") != "1":
            line = f"SKIP  {function.__name__}: sample(s) {', '.join(missing)} not found"
            SKIPPED.append(line)
            print(line)
            return function
        CASES.append(function)
        return function
    return register

@census("hackathon-sheet-platform.yaml", "hackathon-github-platform.yaml", "keep.yaml", "bookstack.yaml")
def the_samples_split_where_measured_and_nowhere_else():
    from cases_boilerplate import find_samples
    from agent import coverage
    samples = find_samples()

    def table(name):
        return coverage._analysis((samples / name).read_text(encoding="utf-8"))

    sheet, github = table("hackathon-sheet-platform.yaml"), table("hackathon-github-platform.yaml")
    assert sum(len(t) for t, _m in sheet.values()) == 199, sum(len(t) for t, _m in sheet.values())
    assert sum(len(t) for t, _m in github.values()) == 488, sum(len(t) for t, _m in github.values())
    assert {"Pressing Enter commits the change;", "Clicking another cell commits the change;"} <= set(sheet["REQ-3-1-1"][0]), sheet["REQ-3-1-1"]
    assert not any(t.startswith("Pressing Enter or clicking") for t in sheet["REQ-3-1-1"][0])

    assert any("refreshing or reopening still shows" in t for t in sheet["REQ-5-3-1"][0]), sheet["REQ-5-3-1"]
    assert {"Closing must not update any branch.", "Reopening must not update any branch."} <= set(github["REQ-6-6"][0])
    for name in ("keep.yaml", "bookstack.yaml"):
        for texts, _m in table(name).values():
            assert all(coverage.alternatives(t) == [t] for t in texts), (name, texts)

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
    print(f"{len(CASES) - failed}/{len(CASES)} layout142 cases passed" + (f" ({len(SKIPPED)} skipped)" if SKIPPED else ""))
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
