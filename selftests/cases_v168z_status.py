from __future__ import annotations

import shutil
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

CASES = []
REL = "frontend/src/components/Status.tsx"
REQ = ("The system stores Archived status and displays an Archived marker on the overview. An Admin can activate the "
       "button to return the record to Active status.")
FORK = """export function Row({ archived, name }: { archived: boolean; name: string }) {
  return (
    <li>
      <span className="font-medium">{name}</span>
      {archived ? <span className="text-sm font-semibold">Archived</span> : null}
    </li>
  );
}
"""

def case(fn):
    CASES.append(fn)
    return fn

def _run(files: dict[str, str], requirement: str = REQ, settings: dict | None = None) -> list[dict]:
    from agent import batch_ui_checks as b

    base = Path(tempfile.mkdtemp())
    try:
        for rel, text in files.items():
            (base / rel).parent.mkdir(parents=True, exist_ok=True)
            (base / rel).write_text(text, encoding="utf-8")
        node = {"audit_written": list(files)}
        return [f for f in b.batch_ui_findings(base, node, requirement, log=lambda *_: None, settings=settings or {})
                if f.get("check") == "status_word"]
    finally:
        shutil.rmtree(base, ignore_errors=True)

@case
def the_fork_snippet_fires_and_names_both_states():
    found = _run({REL: FORK})
    assert len(found) == 1, found
    assert "Archived" in found[0]["problem"] and "Active" in found[0]["fix"], found
    assert found[0]["where"].startswith("status_word " + REL + ":"), found

@case
def other_one_sided_shapes_fire():
    for body in ("{archived && <span>Archived</span>}",
                 "{archived ? 'Archived' : ''}",
                 "{archived ? (<p className=\"x\">Archived</p>) : null}",
                 "{!archived ? null : <em>Archived</em>}"):
        assert _run({REL: FORK.replace("{archived ? <span className=\"text-sm font-semibold\">Archived</span> : null}", body)}), body

@case
def both_words_shown_is_silent():
    for body in ("{archived ? <span>Archived</span> : <span>Active</span>}",
                 "<span>{archived ? 'Archived' : 'Active'}</span>",
                 "{archived && <span>Archived</span>}{!archived && <span>Active</span>}",
                 "{archived ? <span>Archived</span> : null}<span>{LABELS['Active']}</span>"):
        text = FORK.replace("{archived ? <span className=\"text-sm font-semibold\">Archived</span> : null}", body)
        assert not _run({REL: text}), body

@case
def needs_two_named_statuses_and_the_switch():
    assert not _run({REL: FORK}, requirement="The system displays an Archived marker on the overview.")
    assert not _run({REL: FORK}, requirement="Archived status only.")
    assert not _run({REL: FORK}, settings={"status_word_check": False})

    assert not _run({REL: "// {archived ? <span>Archived</span> : null}\nexport const x = 1;\n"})

PAGE = "frontend/src/pages/RepoPage.tsx"

@case
def v181_a_page_split_over_files_is_silent_and_the_fork_alone_still_fires():
    other = "export const Badge = ({ archived }) => (archived ? null : <span>Active</span>);\n"
    assert not _run({REL: FORK, PAGE: other})
    assert len(_run({REL: FORK, PAGE: "export const Page = () => <main>Repository</main>;\n"})) == 1

@case
def v181_translations_and_imported_status_displays_are_not_verified():
    for head in ("import { useTranslation } from 'react-i18next';\nconst { t } = useTranslation();\n",
                 "import { ActiveBadge } from './ActiveBadge';\n",
                 "import { STATUS_LABELS } from '../status';\n",
                 "import StatusPill from './StatusPill';\n"):
        assert not _run({REL: head + FORK}), head
    assert not _run({REL: FORK.replace("<span className=\"text-sm font-semibold\">Archived</span>",
                                       "<span>{t('status.archived')}</span><span>Archived</span>")})

    for head in ("import { Link } from 'react-router-dom';\n", "const s = new Set(); s.add(1); get(1);\n"):
        assert len(_run({REL: head + FORK})) == 1, head

@case
def v181_status_checks_is_no_state():
    assert not _run({REL: FORK}, requirement="Require status checks before merging. The repository has Archived status.")
    assert not _run({REL: FORK}, requirement="Require status checks and Review status checks are listed.")
    found = _run({REL: FORK}, requirement="Require status checks pass. Archived status hides it; Active status shows it.")
    assert len(found) == 1 and "Require" not in found[0]["problem"], found

@case
def the_switch_is_registered_and_on():
    from agent import batch_ui_checks as b
    from agent.settings import _DEFAULTS

    assert "status_word_check" in b.SETTING_KEYS and _DEFAULTS.get("status_word_check") is True

def main() -> int:
    failed = 0
    for fn in CASES:
        try:
            fn()
            print(f"ok    {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc(limit=6)
    print(f"{len(CASES) - failed}/{len(CASES)} v168z status cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
