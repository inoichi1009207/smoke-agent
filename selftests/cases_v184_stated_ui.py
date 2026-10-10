from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.stated_ui_checks import (
    menu_role_findings, stated_menu_entries, stated_textbox_findings, stated_textboxes)

REQ_TEXTBOX = ('The dialog is named "Note for <cell coordinate>" and contains a text box labeled "Note", a "Save note" '
               'button, and, for an existing note, buttons "Edit note" and "Delete note".')
REQ_MENU = ("The visitor signs in as `org-member`, opens “Account menu”, clicks “Your organizations”, and sees the "
            "organization list. The authenticated account menu exposes a visible “Your organizations” link.")

FORK_NOTE = """
function NoteDialog({ coord, existingText, initialMode, onSave, onClose }: Props) {
  const [mode, setMode] = useState<'view' | 'edit'>(initialMode === 'create' ? 'edit' : 'view');
  const [draft, setDraft] = useState(existingText ?? '');
  return (
    <div role="dialog" aria-label={`Note for ${coord}`}>
      <h2>Note for {coord}</h2>
      {error ? (<div role="alert">{error}</div>) : null}
      {mode === 'view' ? (
        <>
          <p className="mb-4 whitespace-pre-wrap">{existingText}</p>
          <div className="flex justify-end gap-2">
            <button type="button" onClick={onClose}>Close</button>
            <button type="button" onClick={() => { setDraft(existingText ?? ''); setMode('edit'); }}>Edit note</button>
          </div>
        </>
      ) : (
        <>
          <div className="mb-4">
            <label htmlFor="note-text-input" className="mb-1 block text-sm">
              Note
            </label>
            <textarea id="note-text-input" value={draft} onChange={(event) => setDraft(event.target.value)} rows={4} />
          </div>
          <button type="button" onClick={handleSave}>Save note</button>
        </>
      )}
    </div>
  );
}
"""

FIXED_NOTE = """
function NoteDialog({ coord, existingText }: Props) {
  const [mode, setMode] = useState<'view' | 'edit'>('view');
  const [draft, setDraft] = useState(existingText ?? '');
  return (
    <div role="dialog" aria-label={`Note for ${coord}`}>
      <label htmlFor="note-text-input">Note</label>
      <textarea id="note-text-input" value={draft} readOnly={mode === 'view'} onChange={(e) => setDraft(e.target.value)} />
      {mode === 'view' ? (
        <button type="button" onClick={() => setMode('edit')}>Edit note</button>
      ) : (
        <button type="button" onClick={handleSave}>Save note</button>
      )}
    </div>
  );
}
"""

AND_NOTE = """
export default function NotePanel({ existing }: Props) {
  return (
    <div role="dialog" aria-label="Note for A1">
      {existing && <p>{existing.text}</p>}
      {!existing && <textarea aria-label="Note" value={draft} onChange={(e) => setDraft(e.target.value)} />}
    </div>
  );
}
"""

RULE_BRANCH = """
export default function RulePanel() {
  return (
    <div role="dialog" aria-label="Rules">
      {ruleType === 'dropdown' ? <input aria-label="Note" value={v} /> : <p>Pick a type</p>}
    </div>
  );
}
"""

FORK_MENU = """
export default function AppHeader() {
  return (
    <header>
                <button type="button" aria-label="Account menu" aria-expanded={menuOpen} onClick={() => setMenuOpen((open) => !open)}>
                  {me.username}
                </button>
                {menuOpen ? (
                  <div className="absolute right-0 z-10 mt-1 w-56 rounded border bg-white py-1 shadow">
                    <Link
                      to="/organizations"
                      className="block px-4 py-2 hover:bg-gray-100"
                      onClick={() => {
                        loadNow(['/organizations']);
                        setMenuOpen(false);
                      }}
                    >
                      Your organizations
                    </Link>
                    <Link to="/settings" className="block px-4 py-2" onClick={() => setMenuOpen(false)}>
                      Settings
                    </Link>
                  </div>
                ) : null}
    </header>
  );
}
"""

FIXED_MENU = FORK_MENU.replace('<div className="absolute right-0', '<div role="menu" className="absolute right-0')
ALWAYS_LIST = """
export default function Side() {
  return (<nav><Link to="/organizations">Your organizations</Link></nav>);
}
"""

def case_requirement_reading():
    assert stated_textboxes(REQ_TEXTBOX) == ["Note"], stated_textboxes(REQ_TEXTBOX)
    assert stated_textboxes('Clicking it displays a text box labeled "Workbook name".') == []
    entries = stated_menu_entries(REQ_MENU)
    assert "Your organizations" in entries and "Account menu" not in entries, entries
    assert stated_menu_entries('Users open "Find and replace" from the "Edit" menu.') == ["Find and replace"]

def case_textbox_fires_on_fork():
    found = stated_textbox_findings({"frontend/src/components/NoteDialog.tsx": FORK_NOTE}, {}, REQ_TEXTBOX)
    assert len(found) == 1 and found[0]["check"] == "stated_textbox", found
    assert "every state" in found[0]["fix"], found[0]["fix"]
    found = stated_textbox_findings({"frontend/src/NotePanel.tsx": AND_NOTE}, {}, REQ_TEXTBOX)
    assert len(found) == 1, found

def case_textbox_silent():
    assert stated_textbox_findings({"frontend/src/N.tsx": FIXED_NOTE}, {}, REQ_TEXTBOX) == []
    assert stated_textbox_findings({"frontend/src/R.tsx": RULE_BRANCH}, {}, REQ_TEXTBOX) == []
    rel = "frontend/src/components/NoteDialog.tsx"
    assert stated_textbox_findings({rel: FORK_NOTE}, {rel: FORK_NOTE}, REQ_TEXTBOX) == []
    assert stated_textbox_findings({rel: FORK_NOTE}, {}, "A text box labeled \"Other\" in the dialog.") == []

def case_menu_fires_on_fork():
    found = menu_role_findings({"frontend/src/components/AppHeader.tsx": FORK_MENU}, {}, REQ_MENU)
    assert len(found) == 1 and found[0]["check"] == "menu_role", found
    assert "role=menu" in found[0]["fix"], found[0]["fix"]

def case_menu_silent():
    assert menu_role_findings({"frontend/src/A.tsx": FIXED_MENU}, {}, REQ_MENU) == []
    assert menu_role_findings({"frontend/src/S.tsx": ALWAYS_LIST}, {}, REQ_MENU) == []
    rel = "frontend/src/A.tsx"
    assert menu_role_findings({rel: FORK_MENU}, {rel: FORK_MENU}, REQ_MENU) == []

SCRATCH = Path(os.environ.get("ARC_V184_SCRATCH", "local-data/scratchpad"))
REQS = Path(os.environ.get("ARC_V184_REQS", "local-data/evolution/req"))
DELIVERABLES = {"github": SCRATCH / "off163/0e93fb7bf95c/template/frontend/src",
                "sheet": SCRATCH / "off163/8333f603a045/template/frontend/src"}
EXPECTED = {("github", "menu_role"): {"REQ-2-1-2"}, ("sheet", "stated_textbox"): {"REQ-8-1-1"}}

def _leaves(node):
    if node.get("type") == "ATOMIC":
        yield node
    for child in node.get("children") or []:
        yield from _leaves(child)

def _leaf_text(leaf) -> str:
    import yaml

    return yaml.safe_dump({k: leaf[k] for k in ("name", "description", "scenarios") if k in leaf},
                          allow_unicode=True, width=10_000)

def _sources(src: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for root, dirs, names in os.walk(src):
        dirs[:] = [d for d in dirs if d not in ("node_modules", "dist")]
        for name in names:
            if name.endswith((".tsx", ".jsx", ".ts", ".js")):
                path = Path(root) / name
                out["frontend/src/" + path.relative_to(src).as_posix()] = path.read_text(encoding="utf-8",
                                                                                           errors="replace")
    return out

def replay() -> dict[tuple[str, str], dict[str, list[str]]]:
    import yaml

    out: dict[tuple[str, str], dict[str, list[str]]] = {}
    for task, src in DELIVERABLES.items():
        tree = yaml.safe_load((REQS / f"hackathon-evolution--{task}" / "requirements.yaml").read_text(encoding="utf-8"))
        files = _sources(src)
        for leaf in _leaves(tree):
            text = _leaf_text(leaf)
            for check, fn in (("stated_textbox", stated_textbox_findings), ("menu_role", menu_role_findings)):
                found = fn(files, {}, text, unbounded=True)
                if found:
                    out.setdefault((task, check), {})[leaf["id"]] = [f["where"] for f in found]
    return out

def case_replay_official_v163():
    fired = replay()
    for (task, check), leaves in sorted(fired.items()):
        for leaf, wheres in sorted(leaves.items()):
            print(f"    replay {task} {check} {leaf}: {wheres}")
    for key, wanted in EXPECTED.items():
        assert wanted <= set(fired.get(key, {})), (key, fired.get(key))

    for (task, check), leaves in fired.items():
        if (task, check) == ("github", "menu_role"):
            for leaf, wheres in leaves.items():
                assert all(w.startswith("menu_role frontend/src/components/AppHeader.tsx:")
                           and int(w.rsplit(":", 1)[1]) in (98, 108, 115) for w in wheres), (leaf, wheres)
        else:
            assert set(leaves) <= EXPECTED.get((task, check), set()), f"unexpected fires: {task} {check} {leaves}"

CASES = [case_requirement_reading, case_textbox_fires_on_fork, case_textbox_silent, case_menu_fires_on_fork,
         case_menu_silent]
if all(p.is_dir() for p in DELIVERABLES.values()) and REQS.is_dir():
    CASES.append(case_replay_official_v163)
else:
    print("SKIP  case_replay_official_v163 (official v163 deliverables or requirements absent)")

def main() -> int:
    failed = 0
    for case in CASES:
        try:
            case()
            print(f"ok    {case.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {case.__name__}")
            traceback.print_exc()
    print(f"{len(CASES) - failed}/{len(CASES)} passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
