from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import traceback
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES = []
SKIPPED: list[str] = []

def case(function):
    CASES.append(function)
    return function

class Skip(unittest.SkipTest):
    pass

def modules_dir() -> Path | None:
    candidates = [os.environ.get("ARC_SELFTEST_FRONTEND_MODULES", ""), str(ROOT / "template" / "frontend"),
                  str(ROOT.parent / "try" / "sheet-v141" / "template" / "frontend")]
    for candidate in candidates:
        if candidate and (Path(candidate) / "node_modules" / "eslint").is_dir() \
                and (Path(candidate) / "node_modules" / "eslint-plugin-react-hooks").is_dir():
            return Path(candidate)
    return None

def need_modules() -> Path:
    import shutil

    modules = modules_dir()
    if modules is None or shutil.which("node") is None:
        raise Skip("no node or no node_modules with eslint + eslint-plugin-react-hooks")
    return modules

def project(files: dict[str, str]) -> tuple[tempfile.TemporaryDirectory, Path]:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp, root

def findings(files: dict[str, str]) -> list[str]:
    from agent import testrun

    modules = need_modules()
    tmp, root = project(files)
    with tmp:
        return testrun.hook_order_errors(root, modules)

EARLY_RETURN = """import { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';

interface Cell { ref: string; value: string }

export default function EditorPage() {
  const { id } = useParams<{ id: string }>();
  const [cells, setCells] = useState<Cell[]>([]);
  useEffect(() => { setCells([]); }, [id]);
  if (!id) {
    return <p>Loading</p>;
  }
  const [selected, setSelected] = useState<string | null>(null);
  return <div>{cells.length}<button onClick={() => setSelected('A1')}>{selected ?? 'none'}</button></div>;
}
"""

CORRECT = """import { useCallback, useMemo, useState } from 'react';

type Row = { id: number; name: string };
enum Mode { View = 'view', Edit = 'edit' }

export function useRows(initial: Row[]): [Row[], (row: Row) => void] {
  const [rows, setRows] = useState<Row[]>(initial);
  const add = useCallback((row: Row) => setRows((old) => [...old, row]), []);
  return [rows, add];
}

export default function ListPage({ seed }: { seed?: Row[] }) {
  const [rows, add] = useRows(seed ?? []);
  const [mode, setMode] = useState<Mode>(Mode.View);
  const first = useMemo(() => (rows[0] as Row | undefined)!, [rows]);
  const config = { size: 3 } satisfies { size: number };
  if (!rows.length) {
    return <p>No rows ({config.size})</p>;
  }
  return (
    <ul>
      {rows.map((row) => <li key={row.id} onClick={() => setMode(Mode.Edit)}>{row.name}</li>)}
      <li>{first?.name} {mode}</li>
      <li><button onClick={() => add({ id: 2, name: 'b' })}>Add</button></li>
    </ul>
  );
}
"""

@case
def a_hook_after_an_early_return_is_one_finding_at_its_line():
    lines = findings({"frontend/src/pages/EditorPage.tsx": EARLY_RETURN})
    assert len(lines) == 1, lines
    assert lines[0].startswith("frontend/src/pages/EditorPage.tsx:13: React Hook \"useState\" is called conditionally"), lines
    assert "early return" in lines[0] and "react-hooks/rules-of-hooks" in lines[0], lines

@case
def a_correct_typescript_page_gives_no_finding():
    assert findings({"frontend/src/pages/ListPage.tsx": CORRECT, "frontend/src/lib/util.ts":
                     "export const pick = <T,>(xs: T[]): T | undefined => xs[0];\nexport type Id = number;\n"}) == []

@case
def a_hook_inside_a_type_assertion_is_still_seen():
    page = CORRECT.replace("  if (!rows.length) {", "  if (seed === undefined) return null;\n"
                           "  const other = (useMemo(() => rows.length, [rows]) as number)!;\n  if (!rows.length || !other) {")
    lines = findings({"frontend/src/pages/ListPage.tsx": page})
    assert len(lines) == 1 and "\"useMemo\" is called conditionally" in lines[0], lines

@case
def test_files_are_let_off():
    spec = "import { renderHook } from '@testing-library/react';\nimport { useRows } from './ListPage';\n" \
           "export const run = () => renderHook(() => useRows([]));\nexport function helper() { return useRows([]); }\n"
    assert findings({"frontend/src/pages/ListPage.test.tsx": spec, "frontend/src/__tests__/x.tsx": spec}) == []

@case
def a_file_that_does_not_parse_is_skipped_and_the_others_are_still_read():
    lines = findings({"frontend/src/pages/Broken.tsx": "export default function Broken( {\n",
                      "frontend/src/pages/EditorPage.tsx": EARLY_RETURN})
    assert len(lines) == 1 and lines[0].startswith("frontend/src/pages/EditorPage.tsx:13:"), lines

@case
def the_finding_reaches_lint_application_and_lint_only_failure():
    from agent import testrun

    modules = need_modules()
    tmp, root = project({"frontend/src/pages/EditorPage.tsx": EARLY_RETURN})
    saved = testrun.hook_order_errors
    try:
        testrun.hook_order_errors = lambda project_dir, modules_from=None: saved(project_dir, modules)
        with tmp:
            lint = testrun.lint_application(root)
    finally:
        testrun.hook_order_errors = saved
    assert "frontend/src/pages/EditorPage.tsx:13: React Hook \"useState\" is called conditionally" in lint, lint
    output = testrun.LINT_HEADER + lint + "\n\n" + testrun.E2E_GREEN_HEAD + "3 passed"
    assert testrun.lint_only_failure(output) == lint

@case
def the_deliverable_of_sheet_v141_gives_no_finding():
    from agent import testrun

    app = ROOT.parent / "try" / "sheet-v141" / "template"
    if not (app / "frontend" / "node_modules" / "eslint-plugin-react-hooks").is_dir():
        raise Skip("no sheet-v141 try copy with its node_modules")
    need_modules()
    assert testrun.hook_order_errors(app) == []

@case
def without_eslint_there_is_no_finding_and_one_diagnostic_line():
    from agent import testrun

    tmp, root = project({"frontend/src/pages/EditorPage.tsx": EARLY_RETURN})
    testrun._hook_order_notes.clear()
    printed = io.StringIO()
    with tmp, contextlib.redirect_stdout(printed):
        assert testrun.hook_order_errors(root) == []
        assert testrun.hook_order_errors(root) == []
        assert "EditorPage" not in testrun.lint_application(root)
    lines = [line for line in printed.getvalue().splitlines() if "rules-of-hooks" in line]
    assert len(lines) == 1 and "not installed" in lines[0], printed.getvalue()

@case
def a_crash_a_timeout_or_a_garbled_answer_gives_no_finding():
    from agent import testrun

    modules = need_modules()
    saved = testrun.run_cmd
    try:
        for answer in [(1, "TypeError: boom"), (0, "not json"), (124, "[timeout after 30s]"), (0, ""),
                       (0, '{"skipped": "modules: Cannot find module \'eslint\'"}')]:
            testrun.run_cmd = lambda *args, answer=answer, **kwargs: answer
            tmp, root = project({"frontend/src/pages/EditorPage.tsx": EARLY_RETURN})
            with tmp, contextlib.redirect_stdout(io.StringIO()):
                assert testrun.hook_order_errors(root, modules) == [], answer
    finally:
        testrun.run_cmd = saved

COUNT_SPEC = "const { test, expect } = require('@playwright/test');\ntest('x', async ({ page }) => {\n%s\n});\n"

def count_verdict(body: str) -> str:
    from agent.testrun import single_element_count_findings

    rejects, hints = single_element_count_findings("backend/test-e2e/x.spec.js", COUNT_SPEC % body)
    return "reject" if rejects else "hint" if hints else "nothing"

@case
def r53_12_a_same_name_in_a_sibling_block_is_no_locator_root():

    assert count_verdict('{ const groups = page.locator("tr"); } { const groups = makeGroups(); '
                         'expect(await groups.filter(Boolean).first().count()).toBe(3); }') == "hint"
    assert count_verdict('expect(await groups.first().count()).toBe(3);\nconst groups = page.locator("tr");') == "nothing"

@case
def r53_12_the_visible_locator_binding_still_rejects():
    assert count_verdict('const groups = page.locator("tr"); expect(await groups.filter(Boolean).first().count()).toBe(3);') == "reject"

    assert count_verdict('{ const groups = makeGroups(); } { const groups = page.locator("tr"); '
                         'expect(await groups.first().count()).toBe(3); }') == "hint"
    assert count_verdict('{ const groups = page.locator("td"); } { const groups = page.locator("tr"); '
                         'expect(await groups.first().count()).toBe(3); }') == "reject"

    assert count_verdict('const groups = page.locator("tr");\n{ const groups = makeGroups(); }\n'
                         'expect(await groups.first().count()).toBe(3);') == "reject"

    assert count_verdict('{ const rows = page.locator("td"); }\nconst rows = makeRows();\nconst shown = rows.filter(Boolean);\n'
                         'expect(await shown.first().count()).toBe(2);') == "hint"
    assert count_verdict('const groups = page.locator("tr");\nif (groups) { expect(await groups.first().count()).toBe(3); }') == "reject"

@case
def r53_12_a_parameter_or_a_reassignment_makes_it_a_hint():
    assert count_verdict('const groups = page.locator("tr");\n'
                         'const check = async (groups) => { expect(await groups.first().count()).toBe(3); };') == "hint"
    assert count_verdict('const groups = page.locator("tr");\n'
                         'async function check(n, groups) { expect(await groups.first().count()).toBe(3); }') == "hint"
    assert count_verdict('let groups = page.locator("tr");\ngroups = makeGroups();\n'
                         'expect(await groups.first().count()).toBe(3);') == "hint"
    assert count_verdict('const groups = page.locator("tr");\nfor (const groups of lists) '
                         '{ expect(await groups.first().count()).toBe(3); }') == "hint"

@case
def r54_3_a_catch_parameter_shadows_the_outer_locator():

    assert count_verdict("const rows = page.locator('tr');\ntry { throw makeGroups(); }\ncatch (rows) {\n"
                         "  expect(await rows.first().count()).toBe(3);\n}") == "hint"
    assert count_verdict("const rows = page.locator('tr');\ntry { throw makeGroups(); }\ncatch ({ rows }) {\n"
                         "  expect(await rows.first().count()).toBe(3);\n}") == "hint"

    assert count_verdict("const rows = page.locator('tr');\ntry { throw makeGroups(); }\ncatch (e) {\n"
                         "  expect(await rows.first().count()).toBe(3);\n}") == "reject"
    assert count_verdict("const rows = page.locator('tr');\ntry { throw makeGroups(); }\ncatch (rows) { }\n"
                         "expect(await rows.first().count()).toBe(3);") == "reject"

    assert count_verdict("const rows = page.locator('tr');\nif (rows) { expect(await rows.first().count()).toBe(3); }") == "reject"

@case
def r54_7_a_var_in_a_closed_block_is_still_the_binding():

    assert count_verdict("if (true) {\n  var rows = page.locator('tr');\n}\nexpect(await rows.first().count()).toBe(3);") == "reject"
    assert count_verdict("for (var i = 0; i < 1; i++) { var rows = page.locator('tr'); }\n"
                         "try { } finally { }\nexpect(await rows.first().count()).toBe(3);") == "reject"

    assert count_verdict("for (var rows = page.locator('tr'); false; ) { }\nexpect(await rows.first().count()).toBe(3);") == "reject"

    assert count_verdict("if (true) {\n  let rows = page.locator('tr');\n}\nexpect(await rows.first().count()).toBe(3);") == "nothing"
    assert count_verdict("if (true) {\n  const rows = page.locator('tr');\n}\nexpect(await rows.first().count()).toBe(3);") == "nothing"

@case
def r54_7_a_var_inside_a_nested_function_is_not_seen_and_an_unclear_scope_is_a_hint():

    assert count_verdict("const f = () => { var rows = page.locator('tr'); };\n"
                         "expect(await rows.first().count()).toBe(3);") == "nothing"
    assert count_verdict("function f() { var rows = page.locator('tr'); }\n"
                         "expect(await rows.first().count()).toBe(3);") == "nothing"

    assert count_verdict("if (true) { var rows = page.locator('tr'); }\n"
                         "const f = async () => { expect(await rows.first().count()).toBe(3); };") == "reject"

    assert count_verdict("for await (const x of xs) { var rows = page.locator('tr'); }\n"
                         "expect(await rows.first().count()).toBe(3);") == "reject"

    assert count_verdict("if (a) { var rows = page.locator('tr'); } else { var rows = makeRows(); }\n"
                         "expect(await rows.first().count()).toBe(3);") == "hint"
    assert count_verdict("var rows = page.locator('tr');\nif (a) { var rows = makeRows(); }\n"
                         "expect(await rows.first().count()).toBe(3);") == "hint"

    assert count_verdict("outer: {\n  var rows = page.locator('tr');\n}\nexpect(await rows.first().count()).toBe(3);") == "hint"

PLAIN_USE = """function useValue(x) { return x; }
export default function Panel({ x }) {
  return <div>{x ? useValue(x) : 0}</div>;
}
"""

def stubbed_hook_lines() -> list[str]:
    import json
    from agent import testrun

    answer = json.dumps({"findings": [{"file": "src/Panel.jsx", "line": 3,
                                       "message": 'React Hook "useValue" is called conditionally. React Hooks must be '
                                                  "called in the exact same order in every component render."}]})
    saved = testrun.run_cmd
    tmp, root = project({"frontend/src/Panel.jsx": PLAIN_USE, "frontend/node_modules/eslint/package.json": "{}",
                         "frontend/node_modules/eslint-plugin-react-hooks/package.json": "{}"})
    try:
        testrun.run_cmd = lambda *args, **kwargs: (0, answer)
        with tmp:
            return testrun.hook_order_errors(root)
    finally:
        testrun.run_cmd = saved

@case
def r54_6_the_hook_note_is_a_possible_risk_not_a_certain_crash():
    lines = stubbed_hook_lines()
    assert len(lines) == 1 and lines[0].startswith('frontend/src/Panel.jsx:3: React Hook "useValue" is called conditionally'), lines
    note = lines[0]
    assert "possible risk" in note and "if this is a real React Hook" in note and "React may throw" in note, note
    assert "needs no change" in note, note
    for certain in ("React throws", "the page goes blank", "Call every hook unconditionally"):
        assert certain not in note, (certain, note)

@case
def r54_6_lint_only_findings_never_turn_a_green_e2e_red():
    import tempfile
    from agent import testrun
    from agent.codegen import CodegenImplementer
    import cases_v146b as b

    lint = "\n".join(stubbed_hook_lines())
    output = testrun.LINT_HEADER + lint + "\n\n" + testrun.E2E_GREEN_HEAD + "3 passed"
    assert testrun.lint_only_failure(output) == lint
    clock = b.Clock()
    with b.patched(clock), b.State(), tempfile.TemporaryDirectory() as tmp:
        checks: list = []
        model = b.Model([b.block(b.PAGE, "first")], clock=clock)
        implementer = CodegenImplementer(model, Path(tmp), b.recording_check([testrun.TestOutcome(False, output)], checks),
                                         log=lambda m: None, max_rounds=1, extra_rounds=0)
        result = implementer.implement(b.NODE, b.TESTS, b.own_red())
    assert result.status == "passed" and len(checks) == 1, (result, checks)

def main() -> int:
    if not __debug__:
        print("FAIL  self-tests need assertions: run without -O and without PYTHONOPTIMIZE")
        return 1
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Skip as why:
            SKIPPED.append(function.__name__)
            print(f"SKIP  {function.__name__}: {why}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=6)
    print(f"{len(CASES) - failed - len(SKIPPED)}/{len(CASES)} v147c cases passed" + (f", {len(SKIPPED)} skipped" if SKIPPED else ""))
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
