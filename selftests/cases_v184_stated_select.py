from __future__ import annotations

import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES = []
REL = "frontend/src/components/ConditionalFormattingDialog.tsx"

REQ = ('The "Format" menu provides "Conditional formatting", opening a dialog named "Conditional formatting". Users '
       'select a target range, choose a rule type from "Condition", enter a required "Value", choose a "Style" option '
       'named "Red fill", "Yellow fill", or "Green fill", and click "Save".')

FORK_STYLE = """export default function D() {
  return (
      <div role="dialog" aria-label="Conditional formatting">
        <div className="mb-3">
          <label htmlFor="cf-condition-select" className="mb-1 block text-sm text-gray-700">
            Condition
          </label>
          <select id="cf-condition-select" value={condition} onChange={(event) => setCondition(event.target.value)}>
            {CONDITIONS.map((option) => (<option key={option} value={option}>{option}</option>))}
          </select>
        </div>
        <div className="mb-3">
          <span className="mb-1 block text-sm text-gray-700">Style</span>
          <div role="listbox" aria-label="Style" className="w-full rounded border border-gray-300 bg-white py-1">
            {STYLES.map((option) => (
              <button
                key={option}
                type="button"
                role="option"
                aria-selected={style === option}
                onClick={() => {
                  setStyle(option);
                  setOpenPicker(null);
                }}
                className="block w-full px-3 py-1 text-left text-sm hover:bg-gray-100"
              >
                {option}
              </button>
            ))}
          </div>
        </div>
      </div>
  );
}
"""

COMBO_POPUP = """export default function D() {
  return (<div role="dialog" aria-label="Conditional formatting">
    <span>Style</span>
    <button type="button" role="combobox" aria-label="Style" aria-expanded={open} onClick={() => setOpen(!open)}>{style}</button>
    {open ? (<div role="listbox" aria-label="Style">{STYLES.map((o) => (<div key={o} role="option">{o}</div>))}</div>) : null}
    <label htmlFor="c">Condition</label><select id="c"><option>Greater than</option></select>
  </div>);
}
"""
NATIVE = """export default function D() {
  return (<div role="dialog" aria-label="Conditional formatting">
    <label htmlFor="c">Condition</label><select id="c"><option>Greater than</option></select>
    <label htmlFor="s">Style</label><select id="s"><option>Red fill</option></select>
  </div>);
}
"""
SHARED = """export default function D() {
  return (<div role="dialog" aria-label="Conditional formatting">
    <ChoiceSelect id="c" label="Condition" value={c} options={CONDS} onChange={setC} />
    <ChoiceSelect id="s" label="Style" value={s} options={STYLES} onChange={setS} />
  </div>);
}
"""

def case(function):
    CASES.append(function)
    return function

def _run(files, requirement=REQ, before=None):
    from agent import batch_ui_checks as b

    base = Path(tempfile.mkdtemp(prefix="v184ss-"))
    try:
        for rel, text in files.items():
            path = base / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        node = {"audit_written": list(files)}
        if before is not None:
            node["audit_written_before"] = before
        found = b.batch_ui_findings(str(base), node, requirement, log=lambda *_: None, settings={})
        return [f for f in found if f.get("check") == "stated_select"]
    finally:
        shutil.rmtree(base, ignore_errors=True)

@case
def fork_style_listbox_fires():
    from agent.batch_ui_checks import stated_selects

    assert stated_selects(REQ) == ["Condition", "Style"], stated_selects(REQ)
    found = _run({REL: FORK_STYLE})
    assert len(found) == 1 and "`Style`" in found[0]["requirement"], found
    assert found[0]["where"].startswith(f"stated_select {REL}:"), found

@case
def combobox_with_popup_listbox_one_finding_at_the_button():
    found = _run({REL: COMBO_POPUP})
    assert len(found) == 1 and "`Style`" in found[0]["requirement"], found
    assert found[0]["where"] == f"stated_select {REL}:4", found

@case
def native_select_and_choice_select_silent():
    assert not _run({REL: NATIVE})
    assert not _run({REL: SHARED})

@case
def inherited_rewritten_file_fires_unchanged_file_silent():
    old = FORK_STYLE.replace("mb-3", "mb-2", 1)
    assert old != FORK_STYLE
    assert len(_run({REL: FORK_STYLE}, before={REL: old})) == 1
    assert not _run({REL: FORK_STYLE}, before={REL: FORK_STYLE})

@case
def native_select_in_another_file_does_not_clear_the_label():
    filt = "frontend/src/components/FilterDialog.tsx"
    combo = COMBO_POPUP.replace('aria-label="Style"', 'aria-label="Condition"').replace(
        '<label htmlFor="c">Condition</label><select id="c"><option>Greater than</option></select>', "")
    found = _run({REL: NATIVE, filt: combo}, requirement='The visitor chooses "Greater than" in "Condition".')
    assert len(found) == 1 and found[0]["where"] == f"stated_select {filt}:4", found
    assert not _run({REL: NATIVE, filt: NATIVE}, requirement='The visitor chooses "Greater than" in "Condition".')

@case
def unstated_label_silent():
    assert not _run({REL: FORK_STYLE}, requirement='A dialog named "Conditional formatting" with "Save".')

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
    print(f"{passed}/{len(CASES)} v184 stated_select cases passed in {time.time() - started:.1f}s")
    return 0 if passed == len(CASES) else 1

if __name__ == "__main__":
    sys.exit(main())
