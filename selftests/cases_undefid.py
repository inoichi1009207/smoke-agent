from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES = []
SKIPPED: list[str] = []

def case(function):
    CASES.append(function)
    return function

def modules_dir() -> Path | None:
    candidates = [os.environ.get("ARC_SELFTEST_FRONTEND_MODULES", ""), str(ROOT / "template" / "frontend"),
                  str(ROOT.parent / "try" / "sheet-v141" / "template" / "frontend")]
    for candidate in candidates:
        if candidate and (Path(candidate) / "node_modules" / "@babel" / "traverse").is_dir() \
                and (Path(candidate) / "node_modules" / "globals").is_dir():
            return Path(candidate)
    return None

class Skip(Exception):
    pass

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

    modules = modules_dir()
    if modules is None:
        raise Skip("no node_modules with @babel/traverse + globals")
    tmp, root = project(files)
    with tmp:
        return testrun.undefined_names(root, modules)

C2_PAGE = """import { useParams } from 'react-router-dom';
import { api } from '../api';

export default function FileEditPage() {
  const { owner, repo } = useParams();
  const file = useData(`/api/repos/${owner}/${repo}/file`);
  return <div><h1>Edit {file?.name}</h1><button onClick={() => api.post('/x')}>Commit changes</button></div>;
}
"""

CLEAN_PAGE = """import * as React from 'react';
import { useState, useEffect, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api';
import type { Repo } from '../types';

interface Props { title: string; children?: ReactNode; repo?: Repo }
type Tone = 'info' | 'error';
enum Mode { View = 'view', Edit = 'edit' }
declare const BUILD_ID: string;

function helper(value: number): string { return String(value); }
const later = () => hoisted();
function hoisted() { return 1; }

export function Panel({ title, children, repo }: Props) {
  const [mode, setMode] = useState<Mode>(Mode.View);
  const navigate = useNavigate();
  const ref = React.useRef<HTMLDivElement | null>(null);
  const tone: Tone = 'info';
  useEffect(() => {
    const id = window.setTimeout(() => setMode(Mode.Edit), 10);
    try { localStorage.setItem('k', JSON.stringify({ tone, v: __APP_VERSION__ })); } catch (err) { console.warn(err); }
    if (typeof maybeGlobal !== 'undefined') { document.title = title; }
    return () => clearTimeout(id);
  }, [title, tone]);
  const data = (repo as unknown as Record<string, unknown>) satisfies object;
  const size = new Intl.NumberFormat('en').format(Number(helper(later())));
  label: for (const x of [1]) { if (x) break label; }
  return (
    <div ref={ref} className="p-2">
      <Link to="/">{title}</Link>
      <React.Fragment>{children}</React.Fragment>
      <span data-x={String(data)}>{size} {mode} {BUILD_ID} {APP_NAME}</span>
      <my-widget><svg><path d="M0 0" /></svg></my-widget>
      <button onClick={() => navigate(-1)}>Back</button>
      <input onChange={(event) => fetch('/api/x?q=' + encodeURIComponent(event.target.value))} />
    </div>
  );
}
"""

@case
def the_c2_shape_is_named_with_its_file_line_and_name():
    found = findings({"frontend/src/pages/FileEditPage.tsx": C2_PAGE})
    assert len(found) == 1, found
    assert found[0].startswith("frontend/src/pages/FileEditPage.tsx:6: `useData` is used here but never imported"), found
    assert "ReferenceError: useData is not defined" in found[0] and "do not remove the call" in found[0], found

@case
def an_unimported_component_and_an_undefined_helper_are_named():
    page = ("import { useState } from 'react';\nexport default function P() {\n  const [a] = useState(0);\n"
            "  const b = formatDate(a);\n  return <main><RepoHeader name={b} /><section>{a}</section></main>;\n}\n")
    found = findings({"frontend/src/pages/P.tsx": page})
    assert len(found) == 2, found
    assert any(line.startswith("frontend/src/pages/P.tsx:4: `formatDate`") for line in found), found
    assert any(line.startswith("frontend/src/pages/P.tsx:5: <RepoHeader>") for line in found), found

@case
def bound_global_type_only_typeof_and_ambient_names_are_not_reported():
    files = {"frontend/src/components/Panel.tsx": CLEAN_PAGE,
             "frontend/src/api/index.ts": "export const api = { post: (u: string) => fetch(u, { method: 'POST' }) };\n",
             "frontend/src/types.ts": "export interface Repo { name: string }\n",
             "frontend/src/vite-env.d.ts": "/// <reference types=\"vite/client\" />\ndeclare const __APP_VERSION__: string;\ndeclare const APP_NAME: string;\n"}
    assert findings(files) == []

    files["frontend/src/components/Panel.tsx"] = CLEAN_PAGE + "export const z = missingThing;\n"
    found = findings(files)
    assert len(found) == 1 and "`missingThing`" in found[0], found

@case
def test_files_are_not_linted():
    spec = "describe('x', () => { it('y', () => { expect(render(<App />)).toBeTruthy(); vi.fn(); }); });\n"
    assert findings({"frontend/src/App.test.tsx": spec, "frontend/src/__tests__/a.tsx": spec,
                     "frontend/src/ok.tsx": "export const ok = 1;\n"}) == []

@case
def the_template_frontend_gives_no_finding():
    from agent import testrun

    modules = modules_dir()
    if modules is None:
        raise Skip("no node_modules with @babel/traverse + globals")
    assert testrun.undefined_names(ROOT / "template", modules) == []

@case
def a_file_that_does_not_parse_is_left_to_the_build():
    assert findings({"frontend/src/Broken.tsx": "export default function B() { return <div>{useData( </div>; }\n",
                     "frontend/src/Fine.tsx": "export const f = () => 1;\n"}) == []

@case
def lint_application_carries_the_findings():
    from agent import testrun

    saved = testrun.undefined_names
    try:
        testrun.undefined_names = lambda project_dir: ["frontend/src/pages/X.tsx:3: `useData` is used here but never imported"]
        with tempfile.TemporaryDirectory() as tmp:
            text = testrun.lint_application(Path(tmp))
        assert "`useData` is used here but never imported" in text, text
    finally:
        testrun.undefined_names = saved

@case
def without_node_modules_nothing_runs_and_nothing_is_reported():
    from agent import testrun

    saved = testrun.run_cmd
    try:
        def boom(*args, **kwargs):
            raise AssertionError("node must not start without @babel/traverse")
        testrun.run_cmd = boom
        tmp, root = project({"frontend/src/pages/FileEditPage.tsx": C2_PAGE})
        with tmp:
            assert testrun.undefined_names(root) == []
    finally:
        testrun.run_cmd = saved

@case
def the_same_sources_are_read_once():
    from agent import testrun

    modules = modules_dir()
    if modules is None:
        raise Skip("no node_modules with @babel/traverse + globals")
    calls = []
    saved = testrun.run_cmd

    def counting(*args, **kwargs):
        calls.append(1)
        return saved(*args, **kwargs)
    try:
        testrun.run_cmd = counting
        tmp, root = project({"frontend/src/pages/Q.tsx": C2_PAGE.replace("FileEditPage", "Q")})
        with tmp:
            first = testrun.undefined_names(root, modules)
            second = testrun.undefined_names(root, modules)
            (root / "frontend/src/pages/Q.tsx").write_text("import { useData } from '../api/store';\n" + C2_PAGE.replace(
                "FileEditPage", "Q"), encoding="utf-8")
            third = testrun.undefined_names(root, modules)
        assert first == second and len(first) == 1 and third == [], (first, third)
        assert len(calls) == 2, calls
    finally:
        testrun.run_cmd = saved

@case
def a_crash_or_a_garbled_answer_gives_no_finding():
    from agent import testrun

    modules = modules_dir()
    if modules is None:
        raise Skip("no node_modules with @babel/traverse + globals")
    saved = testrun.run_cmd
    try:
        for answer in [(1, "TypeError: boom"), (0, "not json"), (124, "[timeout after 120s]"), (0, "")]:
            testrun.run_cmd = lambda *args, answer=answer, **kwargs: answer
            tmp, root = project({"frontend/src/pages/R.tsx": C2_PAGE + "\n// " + repr(answer)})
            with tmp:
                assert testrun.undefined_names(root, modules) == [], answer
    finally:
        testrun.run_cmd = saved

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
    print(f"{len(CASES) - failed - len(SKIPPED)}/{len(CASES)} undefid cases passed" + (f", {len(SKIPPED)} skipped" if SKIPPED else ""))
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
