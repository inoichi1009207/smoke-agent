from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

FIX = HERE / "fixtures" / "v168s_req512"
CASES = []
KEYS = ("dialog_close_check", "dialog_entries_buttons_check", "cell_glyph_check", "stated_select_check",
        "header_button_check", "status_word_check", "count_text_check", "stated_textbox_check", "menu_role_check")

def case(function):
    CASES.append(function)
    return function

def _replay(settings=None, written=None):
    from agent import batch_ui_checks as b
    from agent.detaudit import requirement_text

    node = json.loads((FIX / "node.json").read_text(encoding="utf-8"))
    node["audit_written"] = written if written is not None else sorted(
        p.relative_to(FIX).as_posix() for p in (FIX / "frontend").rglob("*.tsx"))
    return b.batch_ui_findings(FIX, node, requirement_text(node), log=lambda *_: None, settings=settings or {})

def _by(found, check):
    return [f for f in found if f.get("check") == check]

@case
def replay_dialog_close_reports_the_save_dialog_and_not_the_one_with_close():
    found = _by(_replay(), "dialog_close")
    wheres = " ".join(f["where"] for f in found)
    assert "SaveFilterViewDialog.tsx" in wheres, found
    assert "FilterViewsDialog.tsx" not in wheres and "NamedRangesDialog.tsx" not in wheres, wheres
    save = [f for f in found if "SaveFilterViewDialog" in f["where"]][0]
    assert "`Save filter view`" in save["problem"] and "Cancel button does not replace it" in save["problem"]

@case
def replay_dialog_entries_reports_only_the_always_visible_listbox():
    found = _by(_replay(), "dialog_entries")
    assert len(found) == 1 and "FilterViewsDialog.tsx" in found[0]["where"], found
    assert "Saved filter views" in found[0]["problem"]

    for name in ("EditorPage", "FilterDialog", "ValidationDialog", "PivotEditor"):
        assert not any(name + ".tsx" in f["where"] for f in found), (name, found)

@case
def replay_cell_glyph_stated_select_header_button():
    found = _replay()
    glyph = _by(found, "cell_glyph")
    assert len(glyph) == 1 and "WorksheetGrid.tsx" in glyph[0]["where"] and "U+25BE" in glyph[0]["problem"], glyph
    select = _by(found, "stated_select")
    assert len(select) == 1 and "FilterDialog.tsx" in select[0]["where"] and "`Condition`" in select[0]["problem"], select
    header = _by(found, "header_button")
    assert len(header) == 1 and "FilterBar.tsx" in header[0]["where"] and "Filter <header text>" in header[0]["problem"], header

@case
def replay_every_switch_off_and_no_written_list_mean_nothing():
    assert _replay({key: False for key in KEYS}) == []
    for key in KEYS:
        rest = _replay({key: False})
        assert rest and not _by(rest, key.replace("_check", "").replace("_buttons", "")), key
    assert _replay(written=[]) == []
    from agent import batch_ui_checks as b

    node = json.loads((FIX / "node.json").read_text(encoding="utf-8"))
    assert b.batch_ui_findings(FIX, node, "x", log=lambda *_: None, settings={}) == []

@case
def replay_a_file_the_batch_did_not_write_is_never_read():
    found = _replay(written=["frontend/src/components/FilterViewsDialog.tsx"])
    assert [f["check"] for f in found] == ["dialog_entries"], found

DIALOG = """export default function D({ onClose }) {
  return (
    <div role="dialog" aria-label="Rename thing">
      <input aria-label="Name" />
      %s
    </div>
  );
}
"""

def _project(files: dict[str, str]) -> Path:
    base = Path(tempfile.mkdtemp(prefix="v168s-"))
    for rel, text in files.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return base

def _run(files, requirement="", written=None, before=None, settings=None):
    from agent import batch_ui_checks as b

    base = _project(files)
    try:
        node = {"audit_written": list(files) if written is None else written}
        if before is not None:
            node["audit_written_before"] = before
        return b.batch_ui_findings(base, node, requirement, log=lambda *_: None, settings=settings or {})
    finally:
        shutil.rmtree(base, ignore_errors=True)

REL = "frontend/src/components/D.tsx"

@case
def close_by_aria_label_counts_cancel_only_does_not():
    assert not _by(_run({REL: DIALOG % '<button aria-label="Close" onClick={onClose}>x</button>'}), "dialog_close")
    assert not _by(_run({REL: DIALOG % "<button onClick={onClose}>Close</button>"}), "dialog_close")
    assert not _by(_run({REL: DIALOG % "<button onClick={onClose}>{'Close'}</button>"}), "dialog_close")
    found = _by(_run({REL: DIALOG % "<button onClick={onClose}>Cancel</button><button>Save</button>"}), "dialog_close")
    assert len(found) == 1 and "`Rename thing`" in found[0]["problem"], found

    assert _by(_run({REL: DIALOG % "<button>Close account</button>"}), "dialog_close")

    assert _by(_run({REL: "const A = () => <dialog open><h2>Pick</h2><button>OK</button></dialog>;\n"}), "dialog_close")
    assert _by(_run({REL: '<div aria-modal="true" aria-label="M"><button>OK</button></div>\n'}), "dialog_close")

@case
def close_check_switch_off_and_unwritten_file_and_inherited_dialog():
    files = {REL: DIALOG % "<button>Cancel</button>"}
    assert not _run(files, settings={"dialog_close_check": False})
    assert not _run(files, written=["frontend/src/components/Other.tsx"])
    assert not _run(files, before={REL: DIALOG % "<button>Cancel</button><span/>"})
    assert _run(files, before={REL: "export const x = 1;\n"})

    for rel in ("frontend/node_modules/x/D.tsx", "frontend/dist/D.tsx", "frontend/src/components/ChoiceSelect.tsx"):
        assert not _run({rel: DIALOG % "<button>Cancel</button>"}), rel

ENTRIES = """export default function V({ views, open, onClose }) {
  return (
    <div role="dialog" aria-label="Saved things">
      %s
      <button onClick={onClose}>Close</button>
    </div>
  );
}
"""
ALWAYS = """<div role="listbox" aria-label="Things">
        {views.map((v) => <button key={v.id} role="option" aria-selected={false}>{v.name}</button>)}
      </div>"""
POPUP_CONDITIONAL = """<button role="combobox" aria-label="Kind" onClick={() => setOpen(!open)}>{kind}</button>
      {open ? (
        <ul role="listbox" aria-label="Kind">{KINDS.map((k) => <li key={k} role="option">{k}</li>)}</ul>
      ) : null}"""
POPUP_CONTROLLED = """<button aria-haspopup="listbox" aria-expanded={open}>{kind}</button>
      <ul role="listbox" aria-label="Kind" hidden={!open}>{KINDS.map((k) => <li key={k} role="option">{k}</li>)}</ul>"""

@case
def entries_always_visible_reported_popups_not():
    found = _by(_run({REL: ENTRIES % ALWAYS}), "dialog_entries")
    assert len(found) == 1 and "`Things`" in found[0]["problem"], found
    assert not _by(_run({REL: ENTRIES % POPUP_CONDITIONAL}), "dialog_entries")
    assert not _by(_run({REL: ENTRIES % POPUP_CONTROLLED}), "dialog_entries")
    assert not _by(_run({REL: ENTRIES % ("{open && (" + ALWAYS + ")}")}), "dialog_entries")

    assert not _by(_run({REL: "const L = () => (" + ALWAYS.replace("views", "[]") + ");\n"}), "dialog_entries")

@case
def entries_requirement_naming_a_list_there_and_switch_off():
    files = {REL: ENTRIES % ALWAYS}
    assert not _by(_run(files, requirement='"Saved things" shows a list of the saved items.'), "dialog_entries")
    assert not _by(_run(files, requirement='The "Things" listbox holds one option per item.'), "dialog_entries")
    assert _by(_run(files, requirement='"Saved things" offers controls named for the saved items.'), "dialog_entries")
    assert not _by(_run(files, settings={"dialog_entries_buttons_check": False}), "dialog_entries")
    assert not _by(_run(files, before={REL: ENTRIES % ALWAYS}), "dialog_entries")

GRID = """export default function G({ rows }) {
  return (
    <div role="grid">
      {rows.map((r) => (
        <div role="row" key={r.id}>
          <div role="columnheader">Name</div>
          <div role="gridcell" aria-label="Status">%s</div>
        </div>
      ))}
    </div>
  );
}
"""

@case
def cell_glyphs_reported_plain_values_and_names_not():
    for glyph in ("▾", "✎", "⋮", "…", "{'\\u25BE'}", "&#x25BE;"):
        found = _by(_run({REL: GRID % ("{r.status}<span aria-hidden=\"true\">" + glyph + "</span>")}), "cell_glyph")
        assert len(found) == 1, (glyph, found)
    assert not _by(_run({REL: GRID % "{r.status}"}), "cell_glyph")
    assert not _by(_run({REL: GRID % '<button aria-label="Edit ✎">{r.status}</button>'}), "cell_glyph")
    assert not _by(_run({REL: GRID % "{r.status} - 3.5% (x)"}), "cell_glyph")
    assert not _by(_run({REL: GRID % "{r.status}▾"}, settings={"cell_glyph_check": False}), "cell_glyph")
    assert not _by(_run({REL: GRID % "{r.status}▾"}, before={REL: GRID % "▾"}), "cell_glyph")

    assert not _by(_run({REL: GRID.replace(">Name<", ">Name ▾<") % "{r.status}"}), "cell_glyph")

SELECT_REQ = 'The edit dialog provides a combo box labeled "Priority", a text box labeled "Note", and "Save".'
CUSTOM = """export default function E() {
  return (<div>
    <span>Priority</span>
    <button type="button" role="combobox" aria-label="Priority" aria-expanded={open}>{p}</button>
    {open ? <div role="listbox" aria-label="Priority"><div role="option">High</div></div> : null}
  </div>);
}
"""
NATIVE = """export default function E() {
  return (<div>
    <label htmlFor="prio">Priority</label>
    <select id="prio" value={p}><option>High</option></select>
  </div>);
}
"""
SHARED = """export default function E() {
  return (<div><ChoiceSelect id="prio" label="Priority" value={p} options={OPTS} onChange={setP} /></div>);
}
"""

@case
def stated_select_custom_reported_native_and_shared_not():
    assert stated_selects_of(SELECT_REQ) == ["Priority"]
    assert stated_selects_of('Users choose a rule type from "Kind" and click "Save".') == ["Kind"]
    assert stated_selects_of('The visitor chooses "High" in "Priority".') == ["Priority"]
    assert stated_selects_of('choose a "Style" option named "Red"') == ["Style"]
    found = _by(_run({REL: CUSTOM}, requirement=SELECT_REQ), "stated_select")
    assert len(found) == 1 and "`Priority`" in found[0]["problem"], found
    assert not _by(_run({REL: NATIVE}, requirement=SELECT_REQ), "stated_select")
    assert not _by(_run({REL: SHARED}, requirement=SELECT_REQ), "stated_select")

    other = SELECT_REQ + ' The visitor clicks "Priority" to open its options.'
    assert not _by(_run({REL: CUSTOM}, requirement=other), "stated_select")
    assert not _by(_run({REL: CUSTOM}, requirement="A dialog with a text box."), "stated_select")
    assert not _by(_run({REL: CUSTOM}, requirement=SELECT_REQ, settings={"stated_select_check": False}), "stated_select")
    assert not _by(_run({REL: CUSTOM}, requirement=SELECT_REQ, written=[]), "stated_select")
    assert not _by(_run({REL: CUSTOM}, requirement=SELECT_REQ, before={REL: CUSTOM}), "stated_select")

def stated_selects_of(text):
    from agent.batch_ui_checks import stated_selects

    return stated_selects(text)

HEADER_REQ = 'Each header provides a button with the accessible name "Sort <header text>"; the dialog opens.'
BAR = """export default function Bar({ cols }) {
  return (<div>{cols.map((c) => <button key={c} type="button">Sort {c}</button>)}</div>);
}
"""
PAGE = """import Bar from './Bar';
export default function Page({ cols }) {
  return (<main><Bar cols={cols} /><div role="grid"><div role="row">{cols.map((c) => <div role="columnheader" key={c}>{c}</div>)}</div></div></main>);
}
"""
INSIDE = """function HeadButton({ c }) { return <button type="button" aria-label={`Sort ${c}`}>{c}</button>; }
export default function Grid({ cols }) {
  return (<div role="grid"><div role="row">{cols.map((c) => <div role="columnheader" key={c}><HeadButton c={c} /></div>)}</div></div>);
}
"""
TH = """export default function T({ cols }) {
  return (<table><thead><tr>{cols.map((c) => <th key={c}>{c}<button type="button">Sort {c}</button></th>)}</tr></thead></table>);
}
"""

@case
def header_buttons_outside_reported_inside_not():
    bar, page = "frontend/src/components/Bar.tsx", "frontend/src/pages/Page.tsx"
    found = _by(_run({bar: BAR, page: PAGE}, requirement=HEADER_REQ), "header_button")
    assert len(found) == 1 and "Bar.tsx" in found[0]["where"] and "Sort <header text>" in found[0]["problem"], found
    assert not _by(_run({REL: INSIDE}, requirement=HEADER_REQ), "header_button")
    assert not _by(_run({REL: TH}, requirement=HEADER_REQ), "header_button")
    assert not _by(_run({bar: BAR, page: PAGE}, requirement="Each header shows its text."), "header_button")
    assert not _by(_run({bar: BAR, page: PAGE}, requirement=HEADER_REQ, settings={"header_button_check": False}), "header_button")
    assert not _by(_run({bar: BAR, page: PAGE}, requirement=HEADER_REQ, written=[page]), "header_button")
    assert not _by(_run({bar: BAR, page: PAGE}, requirement=HEADER_REQ, before={bar: BAR}), "header_button")

    inside_page = PAGE.replace("<Bar cols={cols} />", "").replace(">{c}</div>)}", "><Bar cols={[c]} /></div>)}")
    assert not _by(_run({bar: BAR, page: inside_page}, requirement=HEADER_REQ), "header_button")

@case
def the_convention_wording_is_generic_and_says_exactly_close():
    from agent import conventions

    line = conventions.V167_LINES[1]
    assert "a button named exactly Close that closes it" in line and "a Cancel button does not replace it" in line
    assert "are buttons named by the entry, not options or menu items, unless the requirement names a list, listbox or options there" in line

    assert "an earlier stage already renders them as another kind (it keeps it)" in line
    assert "the options of a choose-one control (a native select, or the shared choose-one component when one is given) stay as they are" in line
    for literal in ("Filter views", "Queued lanes", "Save filter view", "Saved filter views"):
        assert literal.lower() not in line.lower(), literal

@case
def the_switches_are_registered_and_on():
    from agent.settings import _DEFAULTS

    for key in KEYS:
        assert _DEFAULTS.get(key) is True, key

@case
def wired_into_the_deterministic_layer_and_main():
    import inspect

    from agent import detaudit

    assert "batch_ui_findings(project_dir, node, requirement" in inspect.getsource(detaudit.deterministic_findings)
    main_text = (ROOT / "main.py").read_text(encoding="utf-8")
    assert main_text.index('node["audit_written"] = ') < main_text.index("audit_findings = audit_node(")

CLOSE_CHILD = """function CloseButton({ onClose }) { return <button type="button" onClick={onClose}>Close</button>; }
export default function D({ onClose }) {
  return (<div role="dialog" aria-label="Rename thing"><button>Save</button><CloseButton onClose={onClose} /></div>);
}
"""
FOOTER = """import CloseButton from './CloseButton';
export default function Footer({ onClose }) { return <div className="row"><button>Cancel</button><CloseButton onClose={onClose} /></div>; }
"""
CLOSE_FILE = """export default function CloseButton({ onClose }) { return <button aria-label="Close" onClick={onClose}>x</button>; }
"""
DIALOG_WITH_FOOTER = """import Footer from './Footer';
export default function D({ onClose }) {
  return (<div role="dialog" aria-label="Rename thing"><input aria-label="Name" /><Footer onClose={onClose} /></div>);
}
"""
NO_CLOSE_FOOTER = """export default function Footer({ onClose }) { return <div><button onClick={onClose}>Cancel</button></div>; }
"""

@case
def r2_close_given_by_a_child_component_counts():
    assert not _by(_run({REL: CLOSE_CHILD}), "dialog_close")
    comps = "frontend/src/components/"
    files = {comps + "D.tsx": DIALOG_WITH_FOOTER, comps + "Footer.tsx": FOOTER, comps + "CloseButton.tsx": CLOSE_FILE}
    assert not _by(_run(files, written=[comps + "D.tsx"]), "dialog_close")
    files_bad = {comps + "D.tsx": DIALOG_WITH_FOOTER, comps + "Footer.tsx": NO_CLOSE_FOOTER}
    assert _by(_run(files_bad, written=[comps + "D.tsx"]), "dialog_close")

ENTRIES_FRAGMENT = "{open && <>" + ALWAYS + "</>}"
ENTRIES_SECTION = "{open ? (<section className=\"pane\"><h3>Pick</h3>" + ALWAYS + "</section>) : null}"
ENTRIES_HIDDEN = "<div hidden={!open}>" + ALWAYS + "</div>"
ENTRIES_STYLE = "<div style={{ display: open ? 'block' : 'none' }}>" + ALWAYS + "</div>"
PORTAL = """import { createPortal } from 'react-dom';
export default function V({ views, onClose }) {
  return (
    <div role="dialog" aria-label="Saved things">
      {createPortal(<div role="listbox" aria-label="Things">{views.map((v) => <div key={v} role="option">{v}</div>)}</div>, document.body)}
      <button onClick={onClose}>Close</button>
    </div>
  );
}
"""

@case
def r2_listbox_under_a_condition_anywhere_up_its_ancestry_is_a_popup():
    for body in (ENTRIES_FRAGMENT, ENTRIES_SECTION, ENTRIES_HIDDEN, ENTRIES_STYLE):
        assert not _by(_run({REL: ENTRIES % body}), "dialog_entries"), body
    assert not _by(_run({REL: PORTAL}), "dialog_entries")

    assert _by(_run({REL: ENTRIES % ("<div className=\"box\">" + ALWAYS + "</div>")}), "dialog_entries")
    assert _by(_run({REL: ENTRIES % ALWAYS}), "dialog_entries")

@case
def r2_cell_glyph_only_rendered_literal_text():
    silent = (
        "<span style={{ '--icon': '\"▾\"' }}>{r.status}</span>",
        "<span className=\"after:content-['▾']\">{r.status}</span>",
        "{r.status === '★' ? 'Starred' : r.status}",
        "{r.rating}",
        "{icons['▾']}",
        "{format('★')}",
        "{r.status}{label(r, '▾')}",
    )
    for body in silent:
        assert not _by(_run({REL: GRID % body}), "cell_glyph"), body
    fires = (
        "{r.status}{r.open ? '▾' : ''}",
        "{r.status}{r.editable && <button aria-label=\"Edit\">✎</button>}",
        "{r.status}{'⋮'}",
        "{r.status}{(r.open ? '▾' : '')}",
    )
    for body in fires:
        assert _by(_run({REL: GRID % body}), "cell_glyph"), body

@case
def r2_stated_select_another_interaction_named_is_silent():
    for extra in ('A button with the accessible name "Priority" opens the options.',
                  'The visitor opens "Priority" and chooses "High".',
                  '"Priority" opens a list of the levels.',
                  'The "Priority" button shows the levels.'):
        assert not _by(_run({REL: CUSTOM}, requirement=SELECT_REQ + " " + extra), "stated_select"), extra

    other = SELECT_REQ + ' The visitor opens "Edit", chooses "High" in "Priority", and clicks "Save".'
    assert _by(_run({REL: CUSTOM}, requirement=other), "stated_select")

SORTER = """export default function Sorter({ c }) { return <button type="button">Sort {c}</button>; }
"""
ACTIONS = """import Sorter from './Sorter';
export default function Actions({ c }) { return <span><Sorter c={c} /></span>; }
"""
TABLE = """import Actions from './Actions';
export default function T({ cols }) {
  return (<table><thead><tr>{cols.map((c) => <th key={c}>{c}<Actions c={c} /></th>)}</tr></thead></table>);
}
"""
PORTAL_BAR = """import { createPortal } from 'react-dom';
export default function Bar({ cols, slot }) {
  return createPortal(<div>{cols.map((c) => <button key={c} type="button">Sort {c}</button>)}</div>, slot);
}
"""

@case
def r2_header_button_followed_through_components_and_portal_skipped():
    comps = "frontend/src/components/"
    files = {comps + "T.tsx": TABLE, comps + "Actions.tsx": ACTIONS, comps + "Sorter.tsx": SORTER}
    assert not _by(_run(files, requirement=HEADER_REQ, written=[comps + "Sorter.tsx"]), "header_button")
    assert not _by(_run({comps + "Bar.tsx": PORTAL_BAR}, requirement=HEADER_REQ), "header_button")

    assert _by(_run({comps + "Bar.tsx": BAR, "frontend/src/pages/Page.tsx": PAGE.replace("./Bar", "../components/Bar")},
                    requirement=HEADER_REQ), "header_button")

@case
def r2_header_cost_is_linear_and_capped():
    import time as _time

    def build(n):
        buttons = "".join(f'<button key="k{i}" type="button">Sort {{c{i}}}</button>' for i in range(n))
        return {"frontend/src/components/Bar.tsx": "export default function Bar() { return (<div>" + buttons + "</div>); }\n",
                "frontend/src/pages/Page.tsx": PAGE.replace("./Bar", "../components/Bar")}

    took = []
    for n in (250, 1000):
        started = _time.monotonic()
        assert _by(_run(build(n), requirement=HEADER_REQ), "header_button")
        took.append(_time.monotonic() - started)
    assert took[1] < 5.0, took

    from agent import batch_ui_checks as b

    base = _project(build(50))
    lines = []
    try:
        node = {"audit_written": ["frontend/src/components/Bar.tsx"]}
        got = b.batch_ui_findings(base, node, HEADER_REQ, log=lines.append, settings={}, time_cap=0.0)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    assert isinstance(got, list) and any("time cap" in line and "skipped" in line for line in lines), lines

@case
def r2_main_collects_the_before_texts_only_when_on_and_drops_them_before_spec_context():
    main_text = (ROOT / "main.py").read_text(encoding="utf-8")
    gate = main_text.index("if any_check_on():")
    assert gate < main_text.index('node["audit_written_before"] = ') < main_text.index("audit_findings = audit_node(")
    drop = main_text.index('node.pop("audit_written_before", None)')
    assert drop < main_text.index('self.spec_context[spec] = {"node": node')
    from agent import batch_ui_checks as b

    assert b.any_check_on({}) and not b.any_check_on({key: False for key in KEYS})

def main() -> int:
    passed = 0
    started = time.time()
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
            passed += 1
        except Exception:
            print(f"FAIL  {function.__name__}: {traceback.format_exc(limit=4)}")
    print(f"{passed}/{len(CASES)} v168s batch UI check cases passed in {time.time() - started:.1f}s")
    return 0 if passed == len(CASES) else 1

if __name__ == "__main__":
    sys.exit(main())
