from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.nested_names import nested_control_findings, nested_control_source_findings, parse_aria

NL = chr(10)
CASES = []

def case(function):
    CASES.append(function)
    return function

def page(route, *lines):
    return {"route": route, "url": "http://127.0.0.1:4891" + route, "aria": NL.join(lines)}

SHEET_HEADERS = [
    "- main:",
    "  - grid \"Worksheet grid\":",
    "    - row \"A Filter Region B Filter Sales C\":",
    "      - columnheader",
    "      - columnheader \"A Filter Region\":",
    "        - text: A",
    "        - button \"Filter Region\"",
    "      - columnheader \"B Filter Sales\":",
    "        - text: B",
    "        - button \"Filter Sales\"",
    "      - columnheader \"C\"",
    "    - row \"1 A1 B1 C1\":",
    "      - rowheader \"1\"",
    "      - gridcell \"A1\": Region",
    "      - gridcell \"B1\": Sales",
    "      - gridcell \"C1\"",
]

@case
def snapshot_columnheader_filter_grouped():
    found = nested_control_findings([page("/workbooks/2", *SHEET_HEADERS), page("/workbooks/2", *SHEET_HEADERS)])
    assert len(found) == 1, found
    f = found[0]
    assert f["where"] == "nested_controls: page /workbooks/2", f
    assert 'columnheader "A Filter Region" has a button "Filter Region"' in f["problem"], f
    assert 'own label "A"' in f["problem"] and "1 other columnheader element " in f["problem"], f
    assert "sibling" in f["fix"] and 'aria-label' in f["fix"] and f["requirement"] == "", f

@case
def snapshot_aria_label_exempt():
    found = nested_control_findings([page(
        "/x",
        "- grid:",
        "  - row \"A\":",
        "    - columnheader \"A\":",
        "      - text: A",
        "      - button \"Filter Region\"",
    )])
    assert found == [], found

@case
def snapshot_textbox_in_gridcell_exempt_but_reported_in_header():
    editing = page(
        "/w",
        "- grid \"Worksheet grid\":",
        "  - row \"2 A2 B2\":",
        "    - rowheader \"2\"",
        "    - gridcell \"A2\": East",
        "    - gridcell \"B2\" [selected]:",
        "      - textbox \"Edit B2\": \"10\"",
        "  - row \"3 x 5\":",
        "    - gridcell \"x 5\":",
        "      - text: x",
        "      - textbox: \"5\"",
    )
    assert nested_control_findings([editing]) == []
    header = page(
        "/t",
        "- table:",
        "  - rowgroup:",
        "    - row \"Name foo Size\":",
        "      - columnheader \"Name foo\":",
        "        - text: Name",
        "        - textbox: foo",
        "      - columnheader \"Size\"",
    )
    found = nested_control_findings([header])
    assert len(found) == 1 and 'textbox "foo"' in found[0]["problem"] and 'own label "Name"' in found[0]["problem"], found

@case
def snapshot_listitem_row_sortbutton_checkbox_not_reported():
    found = nested_control_findings([page(
        "/l",
        "- list:",
        "  - listitem:",
        "    - link \"Alpha\":",
        "      - /url: /a",
        "    - button \"Delete\"",
        "  - listitem \"Beta Delete\":",
        "    - text: Beta",
        "    - button \"Delete\"",
        "- table:",
        "  - rowgroup:",
        "    - row \"Name Size\":",
        "      - columnheader \"Name\":",
        "        - button \"Name\"",
        "      - columnheader \"Size\":",
        "        - text: Size",
        "        - checkbox",
        "    - row \"a 1 Edit\":",
        "      - cell \"a\"",
        "      - cell \"1 Edit\":",
        "        - text: \"1\"",
        "        - button \"Edit\"",
    )])
    assert found == [], found

@case
def snapshot_link_card_and_tab_close_reported():
    found = nested_control_findings([page(
        "/",
        "- link \"Repo one Star\":",
        "  - /url: /x",
        "  - text: Repo one",
        "  - button \"Star\"",
        "- link \"Repo two Star\":",
        "  - /url: /y",
        "  - heading \"Repo two\" [level=3]",
        "  - button \"Star\"",
        "- tablist:",
        "  - tab \"Sheet1 Close\" [selected]:",
        "    - text: Sheet1",
        "    - button \"Close\": x",
        "  - 'tab \"Q3: Sales Close\"':",
        "    - text: \"Q3: Sales\"",
        "    - button \"Close\": x",
    )])
    assert len(found) == 2, found
    link = [f for f in found if 'link "Repo one Star"' in f["problem"]]
    tab = [f for f in found if 'tab "Sheet1 Close"' in f["problem"]]
    assert link and "1 other link element " in link[0]["problem"] and 'own label "Repo one"' in link[0]["problem"], found
    assert tab and "1 other tab element " in tab[0]["problem"], found
    nodes = parse_aria("- 'tab \"Q3: Sales Close\"':" + NL + "  - text: \"Q3: Sales\"")
    assert nodes[0]["role"] == "tab" and nodes[0]["name"] == "Q3: Sales Close" and nodes[0]["children"][0]["value"] == "Q3: Sales", nodes

@case
def snapshot_never_raises_and_caps():
    diags = []
    assert nested_control_findings(None) == []
    assert nested_control_findings([None, "x", {"aria": 5}, {"aria": None}], diags) == []
    pages = [page(f"/p{i}", "- link \"Item Star\":", "  - text: Item", "  - button \"Star\"") for i in range(12)]
    assert len(nested_control_findings(pages)) == 8

def project(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp(prefix="v149c-"))
    for rel, text in files.items():
        path = root / "frontend" / "src" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root

def check(root: Path) -> list[dict]:
    diags: list[str] = []
    found = nested_control_source_findings(root, diags)
    assert diags == [], diags
    return found

HEADER_CELL = """import { useRef } from 'react';

function HeaderCell({ role, className, children }: { role: string; className: string; children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  return (
    <div ref={ref} role={role} className={className}>
      {children}
    </div>
  );
}
"""

GRID_USE = """
export default function Grid({ filters, onOpenFilter }: Props) {
  return (
    <div role="grid" aria-label="Worksheet grid">
      <div role="row">
        {COLS.map((col, c) => (
          <HeaderCell
            key={col}
            role="columnheader"%s
            className="w-24"
          >
            {col}
            {filters[c] !== undefined && onOpenFilter ? (
              <button type="button" onClick={() => onOpenFilter(filters[c])}>
                Filter {filters[c]}
              </button>
            ) : null}
          </HeaderCell>
        ))}
      </div>
    </div>
  );
}
"""

@case
def source_v145_header_cell():
    root = project({"components/Grid.tsx": HEADER_CELL + GRID_USE % ""})
    found = check(root)
    assert len(found) == 1, found
    f = found[0]
    line = (HEADER_CELL + GRID_USE).splitlines().index("          <HeaderCell") + 1
    assert f["where"] == f"nested_controls: frontend/src/components/Grid.tsx:{line}", (f, line)
    assert '<HeaderCell role="columnheader">' in f["problem"] and "{col}" in f["problem"] and "<button>" in f["problem"], f
    assert "sibling" in f["fix"] and "aria-label" in f["fix"], f
    labelled = project({"components/Grid.tsx": HEADER_CELL + GRID_USE % (NL + "            aria-label={col}")})
    assert check(labelled) == []
    no_forward = project({"components/Grid.tsx": HEADER_CELL.replace("role={role}", "data-kind={role}") + GRID_USE % ""})
    assert check(no_forward) == []

@case
def source_gridcell_editors_exempt():
    text = """export function Cells({ editing, value, rule }: P) {
  return (
    <div role="row">
      <div role="gridcell">{editing ? <input value={value} onChange={() => {}} /> : value}</div>
      <div role="gridcell">{value}<select value={value}><option>A</option></select></div>
      <div role="gridcell" aria-label={coord}><span>{value}</span><button aria-label="Open dropdown">v</button></div>
    </div>
  );
}
"""
    assert check(project({"Cells.tsx": text})) == []
    bad = text.replace('<div role="gridcell" aria-label={coord}>', '<div role="gridcell">')
    found = check(project({"Cells.tsx": bad}))
    assert len(found) == 1 and "gridcell" in found[0]["problem"] and "Cells.tsx:6" in found[0]["where"], found

@case
def source_listitem_link_not_reported_link_card_reported():
    text = """import { Link } from 'react-router-dom';

export function Repos({ repos }: P) {
  return (
    <ul>
      {repos.map((r) => (
        <li key={r.id}>
          <Link to={`/${r.owner}/${r.name}`}>{r.name}</Link>
          <button onClick={() => remove(r.id)}>Delete</button>
        </li>
      ))}
    </ul>
  );
}
"""
    assert check(project({"Repos.tsx": text})) == []
    card = """import { Link } from 'react-router-dom';

export function Card({ repo }: P) {
  return (
    <Link to={`/${repo.owner}/${repo.name}`} className="card">
      <h3>{repo.name}</h3>
      <button type="button" onClick={(e) => { e.preventDefault(); star(repo); }}>Star</button>
    </Link>
  );
}
"""
    found = check(project({"Card.tsx": card}))
    assert len(found) == 1 and "link" in found[0]["problem"] and '"Star"' in found[0]["problem"], found
    assert found[0]["where"].endswith("Card.tsx:5"), found
    not_router = card.replace("from 'react-router-dom'", "from './ui'")
    assert check(project({"Card.tsx": not_router})) == []

@case
def source_table_headers():
    head = """export function T({ cols, sort }: P) {
  return (
    <table>
      <thead>
        <tr>
          %s
        </tr>
      </thead>
      <tbody><tr><td>x</td></tr></tbody>
    </table>
  );
}
"""
    sort_label = '<th><button onClick={() => sort("name")}>Name</button></th>'
    assert check(project({"T.tsx": head % sort_label})) == []
    with_sort = '<th>{label}<button aria-label="Sort" onClick={sort}>^</button></th>'
    found = check(project({"T.tsx": head % with_sort}))
    assert len(found) == 1 and "columnheader" in found[0]["problem"] and "T.tsx:6" in found[0]["where"], found
    outside = 'export function U() { return (<table><tbody><tr><th>Name<button>Sort</button></th><td>1</td></tr></tbody></table>); }'
    assert check(project({"U.tsx": outside})) == []
    text_input = '<th>Name <input type="text" value={q} onChange={(e) => setQ(e.target.value)} /></th>'
    plain_box = '<th>Name<input type="checkbox" checked={all} onChange={toggle} /></th>'
    assert check(project({"T.tsx": head % text_input})) == []
    assert check(project({"T.tsx": head % plain_box})) == []
    labelled_box = '<th>Name<input type="checkbox" aria-label="Select all" /></th>'
    found = check(project({"T.tsx": head % labelled_box}))
    assert len(found) == 1 and "<input>" in found[0]["problem"], found

@case
def source_portal_custom_conditional_spread_hidden():
    text = """import { createPortal } from 'react-dom';

export function X({ open, editing, name, rest }: P) {
  return (
    <div>
      <button>{name}{open && createPortal(<button>Close</button>, document.body)}</button>
      <button>Save<Tooltip><button>Help</button></Tooltip></button>
      <div role="tab">{editing ? <input value={name} /> : name}<button>Close</button></div>
      <div role="tab" {...rest}>{name}<button>Close</button></div>
      <div role="tab">{name}<button aria-hidden="true">x</button></div>
      <div role="tab">{name}<button><svg viewBox="0 0 1 1" /></button></div>
    </div>
  );
}
"""
    assert check(project({"X.tsx": text})) == []

@case
def source_reader_robustness_and_skips():
    tricky = """import { useState } from 'react';
// <button>commented</button> <div role="tab">
const RE = /<div role="tab">[^<]*<\\/div>/g;
const n = (a: number, b: number) => a < b && b > 0;
type Cb = Array<Record<string, () => void>>;
/* <div role="tab">x<button>y</button></div> */
export function Tabs({ tabs }: { tabs: { id: string; title: string }[] }) {
  const [active, setActive] = useState<string | null>(null);
  const label = `tab ${tabs.length > 1 ? `s:${tabs.length}` : ''}`;
  return (
    <div role="tablist" aria-label={label}>
      {tabs.map((t) => (
        <div key={t.id} role="tab" aria-selected={active === t.id} onClick={() => setActive(t.id)}>
          {t.title}
          <button type="button" onClick={(e) => { e.stopPropagation(); close(t.id); }}>Close {t.title}</button>
        </div>
      ))}
    </div>
  );
}
"""
    found = check(project({"Tabs.tsx": tricky}))
    assert len(found) == 1 and found[0]["where"].endswith("Tabs.tsx:13") and '"Close {t.title}"' in found[0]["problem"], found
    diags = []
    broken = project({"Bad.tsx": "export function B() { return (<div role=\"tab\">x<button>y</button>); }",
                      "test/Tabs.tsx": tricky, "Tabs.test.tsx": tricky})
    assert nested_control_source_findings(broken, diags) == [], diags
    assert any("skipped frontend/src/Bad.tsx" in d for d in diags), diags
    assert nested_control_source_findings(Path(tempfile.mkdtemp())) == []
    assert nested_control_source_findings("Z:/no/such/dir") == []

SORT_HEAD = [
    "- table:",
    "  - rowgroup:",
    "    - row \"Name Sort ascending Size\":",
    "      - columnheader \"Name Sort ascending\":",
    "        - text: Name",
    "        - button \"Sort ascending\"",
    "      - columnheader \"Size\"",
]

TH_SOURCE = """export function T({ rows, sort }: P) {
  return (
    <table>
      <thead>
        <tr>
          %s
        </tr>
      </thead>
      <tbody><tr><td>x</td></tr></tbody>
    </table>
  );
}
"""

@case
def requirement_quoting_whole_name_suppresses():
    pages = [page("/t", *SORT_HEAD)]
    whole = 'The first column header is named “Name Sort ascending” and sorts the table.'
    assert nested_control_findings(pages, requirement=whole) == []
    assert len(nested_control_findings(pages, requirement='The column header "Name" sorts the table.')) == 1
    assert len(nested_control_findings(pages, requirement="")) == 1
    sheet = 'Column headers are lettered ("A", "B", ...); a "Filter" button opens the filter.'
    assert len(nested_control_findings([page("/workbooks/2", *SHEET_HEADERS)], requirement=sheet)) == 1
    literal = TH_SOURCE % '<th>Name<button onClick={sort}>Sort ascending</button></th>'
    root = project({"T.tsx": literal})
    diags: list[str] = []
    assert nested_control_source_findings(root, diags, requirement=whole) == [], diags
    assert any("quotes the whole name" in d for d in diags), diags
    assert len(check(root)) == 1
    assert len(nested_control_source_findings(root, requirement='The column header "Name" sorts.')) == 1
    mixed = project({"T.tsx": TH_SOURCE % '<th>{label}<button onClick={sort}>Sort {dir}</button></th>'})
    assert nested_control_source_findings(mixed, requirement=whole) == []
    assert len(nested_control_source_findings(mixed, requirement='Header "Name" and button "Sort ascending".')) == 1
    exprs = project({"T.tsx": TH_SOURCE % '<th>{label}<button onClick={sort}>{sortLabel}</button></th>'})
    assert len(nested_control_source_findings(exprs, requirement=whole)) == 1
    v145 = project({"components/Grid.tsx": HEADER_CELL + GRID_USE % ""})
    assert len(nested_control_source_findings(v145, requirement=sheet)) == 1

@case
def hidden_inner_controls_not_reported():
    hidden = [
        '<th>Name<button hidden>Filter</button></th>',
        '<th>Name<button hidden={true}>Filter</button></th>',
        '<th>Name<button hidden={!open}>Filter</button></th>',
        '<th>Name<button style={{ display: "none" }}>Filter</button></th>',
        '<th>Name<button style={{ display: open ? "inline" : "none" }}>Filter</button></th>',
        '<th>Name<button style={{ visibility: "hidden" }}>Filter</button></th>',
        '<th>Name<button className="hidden">Filter</button></th>',
        '<th>Name<button className="ml-1 md:hidden">Filter</button></th>',
        '<th>Name<button className="invisible">Filter</button></th>',
        '<th>Name<button className={open ? "block" : "hidden"}>Filter</button></th>',
        '<th>Name<span hidden><button>Filter</button></span></th>',
        '<th>Name<span style={{ display: "none" }}><button>Filter</button></span></th>',
    ]
    for markup in hidden:
        assert check(project({"T.tsx": TH_SOURCE % markup})) == [], markup
    shown = [
        '<th>Name<button hidden={false}>Filter</button></th>',
        '<th>Name<button className="overflow-hidden px-1">Filter</button></th>',
        '<th>Name<button className="sr-only">Filter</button></th>',
        '<th>Name<button style={{ color: "red" }}>Filter</button></th>',
    ]
    for markup in shown:
        assert len(check(project({"T.tsx": TH_SOURCE % markup}))) == 1, markup

    assert nested_control_findings([page(
        "/t",
        "- table:",
        "  - rowgroup:",
        "    - row \"Name Size\":",
        "      - columnheader \"Name\"",
        "      - columnheader \"Size\"",
    )]) == []

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=4)
    print(f"{len(CASES) - failed}/{len(CASES)} self-tests passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
