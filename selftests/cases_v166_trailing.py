from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cases_v145b import NODE, TESTS, State, green, own_red
from cases_v160c_edits import BIG, NEW7, OLD7, SMALL, Model, edit, implementer, project, whole

from agent.edits import EDIT_END, FILE_END, TRAILING_MARKERS, parse_answer, strip_trailing_markers

NL = chr(10)
BT = chr(96)
CASES = []
PANEL = "frontend/src/components/FilterViewsPanel.tsx"
PANEL_BODY = NL.join(["import React from 'react';", "", "export default function FilterViewsPanel() {",
                      "  return <div className=\"filter-views\">Filter views</div>;", "}"])

def case(function):
    CASES.append(function)
    return function

class Switch:

    def __init__(self, on: bool):
        self.on = on

    def __enter__(self):
        from agent.settings import SETTINGS

        self.saved = SETTINGS.get("strip_trailing_marker")
        SETTINGS["strip_trailing_marker"] = self.on
        return self

    def __exit__(self, *exc):
        from agent.settings import SETTINGS

        if self.saved is None:
            SETTINGS.pop("strip_trailing_marker", None)
        else:
            SETTINGS["strip_trailing_marker"] = self.saved
        return False

def incident_answer() -> str:
    return NL.join([edit(BIG, (OLD7, NEW7)), f"=== FILE: {PANEL} ===", PANEL_BODY, "=== END EDIT ===",
                    edit(SMALL, ("export default function E() { return null; }", "export default function E() { return 1; }"))])

def run(answer: str, on: bool) -> tuple[dict[str, str], list[str], object]:
    with tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        logs: list[str] = []
        with State(), Switch(on):
            result = implementer(Model([(answer, "stop")]), root, [green()], logs).implement(NODE, TESTS, own_red())
        files = {rel: (root / rel).read_text(encoding="utf-8") for rel in (PANEL, BIG, SMALL) if (root / rel).is_file()}
    return files, logs, result

@case
def the_switch_is_registered_and_on():
    from agent import settings

    assert settings._DEFAULTS["strip_trailing_marker"] is True

@case
def the_markers_are_exactly_the_parsers_bare_end_forms():
    for marker in TRAILING_MARKERS:
        regex = FILE_END if "FILE" in marker else EDIT_END
        found = regex.search(marker)
        assert found and found.group(0) == marker, marker

    assert not any(m.startswith("=== FILE") or m.startswith("=== EDIT") or set(m) == {"="} for m in TRAILING_MARKERS)

@case
def only_code_files_are_cleaned():
    from agent.edits import TRAILING_MARKER_CODE_SUFFIXES as S
    assert "x.tsx".endswith(S) and "a/b.json".endswith(S) and "c.css".endswith(S)
    assert not "README.md".endswith(S) and not "notes.txt".endswith(S) and not "page.html".endswith(S)

@case
def the_incident_shape_loses_its_trailing_marker():
    body, removed = strip_trailing_markers(PANEL_BODY + NL + "=== END EDIT ===" + NL)
    assert body == PANEL_BODY + NL and removed == ["=== END EDIT ==="], (body, removed)

    body, removed = strip_trailing_markers(PANEL_BODY + NL + NL + "  === END OF EDIT ===  " + NL + NL + "=== END FILE ===" + NL)

    assert body == PANEL_BODY + NL + NL and removed == ["=== END OF EDIT ===", "=== END FILE ==="], (body, removed)

    body, removed = strip_trailing_markers("text  " + NL + NL + "=== END EDIT ===" + NL)
    assert body == "text  " + NL + NL and removed == ["=== END EDIT ==="], (body, removed)

@case
def codex_v164_counterexamples_stay_untouched():
    bodies = [

        NL.join(["/*", "old comment", "=== END EDIT === */"]) + NL,
        NL.join(["/*", "old comment", "=== END OF FILE === */"]) + NL,

        NL.join(["const x = " + BT, "=== END EDIT ===" + BT + ";"]) + NL,

        NL.join(["{", '  "note": "=== END FILE ==="', "}"]) + NL,
        '"=== END EDIT ==="' + NL,

        NL.join(["export const D = () => (", "  <div>", "=======", "  </div>", ");"]) + NL,
        NL.join(["<div>", "======="]) + NL,

        NL.join(["# Protocol", "", "```", "=== END EDIT ===", "```"]) + NL,

        NL.join(["const a = 1;", "=== END EDIT ===", "const b = 2;"]) + NL,

        "const a = 1;" + NL + "// === END EDIT ===" + NL,
        "const a = 1;" + NL + "=== END EDIT ===;" + NL,
        "const a = 1;" + NL + "=== END EDIT === here" + NL,
        "const a = 1;" + NL + "==== END EDIT ====" + NL,

        "=== END EDIT ===" + NL,
        NL + "=== END EDIT ===" + NL + "=== END FILE ===" + NL,
    ]
    for body in bodies:
        assert strip_trailing_markers(body) == (body, []), body

@case
def the_incident_answer_writes_the_file_without_the_marker_and_says_so():
    answer = incident_answer()
    parsed = parse_answer(answer, True)
    panel = [b for b in parsed.blocks if b.path == PANEL]
    assert panel and panel[0].body.rstrip().endswith("=== END EDIT ==="), parsed.blocks
    files, logs, result = run(answer, True)
    text = files[PANEL]
    assert text == PANEL_BODY + NL, text[-200:]
    assert NEW7 in files[BIG]
    line = f"[codegen] REQ-9: removed trailing protocol line '=== END EDIT ===' from {PANEL}"
    assert line in logs, [x for x in logs if "removed" in x]
    assert result.status == "passed", result

@case
def switch_off_writes_what_v163_wrote_byte_for_byte():
    answer = incident_answer()
    v163_body = [b for b in parse_answer(answer, True).blocks if b.path == PANEL][0].body
    files, logs, _result = run(answer, False)
    assert files[PANEL] == v163_body
    assert not [x for x in logs if "removed trailing protocol line" in x]

@case
def counterexample_bodies_in_a_file_block_are_written_as_v163_wrote_them():
    for body in (NL.join(["const x = " + BT, "=== END EDIT ===" + BT + ";"]),
                 NL.join(["export const D = () => (", "  <div>", "=======", "  </div>", ");"]),
                 NL.join(["const a = 1;", "=== END EDIT ===", "const b = 2;"]),
                 NL.join(["/*", "old comment", "=== END EDIT === */", "const answer = 42;"])):
        answer = NL.join([edit(BIG, (OLD7, NEW7)), whole(PANEL, body)])
        v163_body = [b for b in parse_answer(answer, True).blocks if b.path == PANEL][0].body
        files, logs, _result = run(answer, True)
        assert files[PANEL] == v163_body, body
        assert not [x for x in logs if "removed trailing protocol line" in x], body

@case
def an_edit_replace_part_is_never_touched():

    answer = edit(BIG, (OLD7, NEW7 + NL + "=== END FILE ==="))
    files, logs, _result = run(answer, True)
    text = files[BIG]
    assert NEW7 + NL + "=== END FILE ===" + NL in text, text[:400]
    assert not [x for x in logs if "removed trailing protocol line" in x]

@case
def the_plain_whole_file_path_is_never_touched():

    answer = whole(PANEL, PANEL_BODY + NL + "=== END EDIT ===")
    files, logs, _result = run(answer, True)
    text = files[PANEL]
    assert text.rstrip().endswith("=== END EDIT ==="), text[-200:]
    assert not [x for x in logs if "removed trailing protocol line" in x]

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
    print(f"{len(CASES) - failed}/{len(CASES)} v166a trailing-marker cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
