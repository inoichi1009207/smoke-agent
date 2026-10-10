from __future__ import annotations

import inspect
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NL = chr(10)
CASES = []

def case(function):
    CASES.append(function)
    return function

def _finding(where: str, problem: str, layer: str = "scripted") -> dict:
    return {"where": where, "requirement": "(r)", "problem": problem, "fix": "f", "layer": layer}

@case
def a_repair_gets_at_most_six_findings_scripted_first_each_once():
    from agent.audit import MAX_REPAIR_FINDINGS, finding_fingerprint, select_for_repair
    assert MAX_REPAIR_FINDINGS == 6
    model = [_finding("route /", f"model claim {c}", "model") for c in "ab"]
    scripted = [_finding(f"frontend/src/P{c}.tsx:1", f"scripted problem {c}") for c in "abcdefg"]
    now, repeated, later = select_for_repair(model + scripted, set())

    assert [f["problem"] for f in now] == [f"scripted problem {c}" for c in "abcdef"], now
    assert [f["problem"] for f in later] == ["scripted problem g", "model claim a", "model claim b"] and not repeated

    sent = {finding_fingerprint(f) for f in now}
    moved = _finding("frontend/src/Pa.tsx:40", "scripted problem a")
    assert finding_fingerprint(moved) in sent
    now2, repeated2, later2 = select_for_repair([moved, scripted[6]] + model, sent)
    assert [f["problem"] for f in repeated2] == ["scripted problem a"], repeated2
    assert [f["problem"] for f in now2] == ["scripted problem g", "model claim a", "model claim b"] and not later2

    twice = select_for_repair([_finding("route /", "x 3 of 5"), _finding("Route /", "X  3 OF 5")], set())[0]
    assert len(twice) == 1
    assert finding_fingerprint(_finding("route /", "x")) != finding_fingerprint(_finding("route /b", "x"))

@case
def a_fingerprint_keeps_business_numbers_and_drops_lines_times_and_durations():
    from agent.audit import finding_fingerprint as fp

    assert fp(_finding("route /issues/41", "issue 41 lacks its title")) != fp(_finding("route /issues/42", "issue 42 lacks its title"))
    assert fp(_finding("route /", "31 of 31 rows are stacked")) != fp(_finding("route /", "16 of 16 rows are stacked"))
    assert fp(_finding("route /", "cell A1 shows 1200")) != fp(_finding("route /", "cell A1 shows 800"))

    assert fp(_finding("frontend/src/Pa.tsx:12", "p")) == fp(_finding("frontend/src/Pa.tsx:40:7", "p"))
    assert fp(_finding("frontend/src/Pa.tsx:12", "p")) != fp(_finding("frontend/src/Pb.tsx:12", "p"))

    assert fp(_finding("route /", "Q3 SalesLast updated: 2026-09-27T15:41:08.865Z glued")) == \
        fp(_finding("route /", "Q3 SalesLast updated: 2026-09-28T01:02:03.004Z glued"))
    assert fp(_finding("route /", "shown 208 ms after the click")) == fp(_finding("route /", "shown 1500ms after the click"))

    assert fp(_finding("route /", "a cool-down of 5 seconds is missing")) != fp(_finding("route /", "a cool-down of 10 seconds is missing"))
    assert fp(_finding("route /", "locks after 2 min")) != fp(_finding("route /", "locks after 5 min"))
    assert fp(_finding("route /", "at 10:41:08 the list")) == fp(_finding("route /", "at 11:02:59 the list"))

@case
def a_finding_is_booked_as_sent_only_when_its_repair_is_kept():
    import main as factory_module
    source = inspect.getsource(factory_module.Factory.process_node)
    booking = "self.audit_sent_fingerprints = list(dict.fromkeys("
    assert source.count(booking) == 1, source.count(booking)
    kept = source.index('if fix.status == "passed":')
    rolled_back = source.index("restored = self.restore_sources(before_audit)")
    assert kept < source.index(booking) < rolled_back
    assert source.index("select_for_repair(audit_findings, sent_before + given_up)") < kept

    rollback_booking = "self.audit_rolled_back_fingerprints = ("
    assert source.count(rollback_booking) == 1
    assert rolled_back < source.index(rollback_booking)
    assert "rolled.count(fp) >= self.AUDIT_ROLLBACKS_MAX" in source and factory_module.Factory.AUDIT_ROLLBACKS_MAX == 2

@case
def the_audit_repair_sends_the_brief_once_and_remembers_what_it_sent():
    import main as factory_module
    source = inspect.getsource(factory_module.Factory.process_node)
    assert "extra_context=brief" in source and "TestOutcome(False, brief)" not in source
    assert "select_for_repair(audit_findings, sent_before + given_up)" in source and "repeat_logged" in source
    assert "audit_sent_fingerprints" in factory_module.Factory.CHECKPOINT_FIELDS
    assert "audit_rolled_back_fingerprints" in factory_module.Factory.CHECKPOINT_FIELDS
    assert "self.audit_sent_fingerprints: list[str] = []" in inspect.getsource(factory_module.Factory.__init__)

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "app"
        (out / ".arc").mkdir(parents=True)
        dict_fields = ("spec_context", "file_specs", "test_snapshot", "test_ids_by_file", "test_titles", "report", "seed_checks")
        first = factory_module.Factory.__new__(factory_module.Factory)
        first.project_dir, first.revised_specs = out, set()
        for field in factory_module.Factory.CHECKPOINT_FIELDS:
            setattr(first, field, {} if field in dict_fields else [])
        first.started, first.spent_before = __import__("time").time(), 0.0
        first.audit_sent_fingerprints = ["0123456789abcdef", "fedcba9876543210"]
        first.save_checkpoint("digest-1", ["REQ-1"], "")
        second = factory_module.Factory.__new__(factory_module.Factory)
        second.project_dir, second.revised_specs = out, set()
        second.audit_sent_fingerprints = []
        assert second.load_checkpoint("digest-1") == ["REQ-1"]
        assert second.audit_sent_fingerprints == ["0123456789abcdef", "fedcba9876543210"]

@case
def a_glued_name_is_reported_only_when_the_rest_is_ui_metadata():
    from agent import detaudit
    names = ["Q3 Sales", "Budget"]
    entries = [("/", "link", "Q3 SalesLast updated: 2026-09-27T15:41:08.865Z"),
               ("/", "link", "Q3 SalesTeam"),
               ("/", "link", "Q3 SalesQ4"),
               ("/", "button", "BudgetUpdated 2026-09-27"),
               ("/", "button", "BudgetEdited 9/27/2026"),
               ("/", "link", "BudgetOwner：alice")]
    found = detaudit.glued_names(entries, names, "the requirement")
    assert [name for _, _, name, _ in found] == [
        "Q3 SalesLast updated: 2026-09-27T15:41:08.865Z", "BudgetUpdated 2026-09-27", "BudgetEdited 9/27/2026",
        "BudgetOwner：alice"], found
    for rest in ("Team", "Q4", "Report", "s2"):
        assert not detaudit.UI_METADATA.search(rest), rest

_EFFECT_APP = NL.join([
    "import { useEffect, useState } from 'react';",
    "export default function App() {",
    "  const [editing, setEditing] = useState(true);",
    "  EFFECT",
    "  return <div>",
    "    {editing && (<div role=\"dialog\">",
    "      <button type=\"button\" onClick={() => setEditing(false)}>Save changes</button>",
    "    </div>)}",
    "  </div>;",
    "}",
])

@case
def a_setter_whose_state_an_effect_reads_is_not_inert():
    from agent import inert_controls
    if inert_controls.tsx_parser() is None:
        return
    requirement = 'The user clicks "Save changes" to store the note.'
    effects = {
        "": 1,
        "useEffect(() => { if (!editing) saveNote(); }, [editing]);": 0,
        "useEffect(() => { if (!editing) saveNote(); });": 0,
        "React.useEffect(() => { track(); }, [editing]);": 0,
        "useLayoutEffect(() => { if (!editing) saveNote(); }, [editing]);": 0,
    }
    for effect, expected in effects.items():
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "frontend" / "src").mkdir(parents=True)
            (root / "frontend" / "src" / "App.tsx").write_text(_EFFECT_APP.replace("EFFECT", effect) + NL, encoding="utf-8")
            found = [f for f in inert_controls.inert_named_controls(requirement, root, log=lambda m: None) if f["kind"] == "inert"]
            assert len(found) == expected, (effect, found)

@case
def the_parser_directory_is_per_run_and_joins_sys_path_only_when_needed():
    from agent import inert_controls
    saved_state, saved_import, saved_path = dict(inert_controls._STATE), inert_controls._import_light_pair, list(sys.path)
    had_pack, pack = "tree_sitter_language_pack" in sys.modules, sys.modules.get("tree_sitter_language_pack")
    parser_modules = {m: sys.modules[m] for m in list(sys.modules) if m.split(".")[0] in inert_controls.PARSER_MODULES}
    made = []
    try:
        sys.modules["tree_sitter_language_pack"] = None
        inert_controls._STATE.update(pydeps=None, parser=None)
        assert inert_controls.pydeps_dir() is None
        first = inert_controls.pydeps_dir(create=True)
        made.append(first)
        assert first.is_dir() and first.name.startswith("arc-agent-pydeps-") and inert_controls.pydeps_dir(create=True) == first
        assert Path(tempfile.gettempdir()) / "arc-agent-pydeps" != first

        inert_controls._import_light_pair = lambda: "env-parser"
        assert inert_controls._load_parser() == "env-parser" and str(first) not in sys.path

        calls = []

        def fails_then_works():
            calls.append(str(first) in sys.path)
            if not calls[-1]:
                raise ImportError("no tree_sitter")
            return "installed-parser"
        inert_controls._import_light_pair = fails_then_works
        assert inert_controls._load_parser() == "installed-parser" and calls == [False, True], calls
        assert sys.path[0] == str(first)

        sys.path[:] = saved_path
        inert_controls._STATE.update(pydeps=None)
        inert_controls._import_light_pair = lambda: (_ for _ in ()).throw(ImportError("no tree_sitter"))
        assert inert_controls._load_parser() is None and not any("arc-agent-pydeps" in p for p in sys.path)
        source = inspect.getsource(inert_controls.install_parser_in_background)
        assert "pydeps_dir(create=True)" in source and "PYDEPS_DIR" not in inspect.getsource(inert_controls)
    finally:
        sys.path[:] = saved_path
        if had_pack:
            sys.modules["tree_sitter_language_pack"] = pack
        else:
            sys.modules.pop("tree_sitter_language_pack", None)
        sys.modules.update(parser_modules)
        inert_controls._import_light_pair = saved_import
        inert_controls._STATE.clear()
        inert_controls._STATE.update(saved_state)
        for path in made:
            shutil.rmtree(path, ignore_errors=True)

def _route_project(root: Path, pages: dict[str, str], app_extra: str = "") -> None:
    (root / "backend" / "src" / "routes").mkdir(parents=True)
    (root / "frontend" / "src" / "pages").mkdir(parents=True)
    (root / "frontend" / "src" / "api").mkdir(parents=True)
    (root / "backend" / "src" / "app.js").write_text(
        "app.use('/api/workbooks', require('./routes/workbooks'));\napp.get('/api/health', h);\n" + app_extra, encoding="utf-8")
    (root / "backend" / "src" / "routes" / "workbooks.js").write_text(
        "router.get('/', a);\nrouter.post('/', b);\nrouter.get('/:id', c);\n", encoding="utf-8")
    (root / "frontend" / "src" / "api" / "index.ts").write_text(
        "import axios from 'axios';\nconst apiClient = axios.create({ baseURL: '/api' });\nexport const http = apiClient;\n"
        "export function list() { return apiClient.get('/workbooks'); }\nexport function gone() { return apiClient.get('/nothing'); }\n"
        "export default apiClient;\n", encoding="utf-8")
    (root / "frontend" / "src" / "api" / "helpers.ts").write_text("export const api = { get: (p: string) => p };\n", encoding="utf-8")
    for rel, text in pages.items():
        (root / "frontend" / "src" / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / "frontend" / "src" / rel).write_text(text, encoding="utf-8")

@case
def a_client_call_counts_only_on_an_imported_axios_instance():
    from agent import detaudit
    pages = {
        "pages/Editor.tsx": "import apiClient from '../api';\napiClient.post(`/workbooks/${id}/undo`, {});\n",
        "pages/Alias.tsx": "import client from '@/api/index';\nclient.post('/workbooks/1/redo');\n",
        "pages/Named.tsx": "import { http as net } from '../api/index';\nnet.delete('/workbooks/1');\n",
        "pages/Plain.tsx": "apiClient.post('/workbooks/1/plain');\nclient.post('/workbooks/1/plain');\n",
        "pages/Other.tsx": "import { api } from '../api/helpers';\napi.get('/workbooks/1/other');\n",
        "pages/Star.tsx": "import * as api from '../api';\napi.get('/workbooks/1/star');\n",
        "pages/Raw.tsx": "import axios from 'axios';\naxios.get('/workbooks/1/raw');\n",
        "pages/Member.tsx": "import apiClient from '../api';\nthis.apiClient.get('/workbooks/1/member');\n",
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _route_project(root, pages)
        found = detaudit.unanswered_api_calls(root)
        assert sorted(found) == sorted([
            ("POST", "/api/workbooks/1/redo", "frontend/src/pages/Alias.tsx:2"),
            ("POST", "/api/workbooks/*/undo", "frontend/src/pages/Editor.tsx:2"),
            ("DELETE", "/api/workbooks/1", "frontend/src/pages/Named.tsx:2"),
            ("GET", "/api/nothing", "frontend/src/api/index.ts:5"),
        ]), found

        (root / "frontend" / "src" / "Top.tsx").write_text("import apiClient from './api/index';\napiClient.patch('/workbooks/2');\n", encoding="utf-8")
        assert ("PATCH", "/api/workbooks/2", "frontend/src/Top.tsx:2") in detaudit.unanswered_api_calls(root)

@case
def comments_are_lexed_out_and_strings_are_left_alone():
    from agent import detaudit
    lex = detaudit.strip_js_comments
    assert lex("a(); // router.post('/x')\nb();") == "a(); \nb();"
    assert lex("const u = 'http://x.y/z'; f(); // gone") == "const u = 'http://x.y/z'; f(); "
    assert lex('const t = `a//b ${c /* not a comment inside ${} is still code */}`;') == 'const t = `a//b ${c /* not a comment inside ${} is still code */}`;'
    assert lex("x /* one\ntwo */ y") == "x  \n y" and lex("'it\\'s // here' // c") == "'it\\'s // here' "
    assert lex("const re = /'/; g(); // c\nnext();") .endswith("\nnext();")
    pages = {"pages/Editor.tsx": "import apiClient from '../api';\napiClient.post(`/workbooks/${id}/undo`, {}); // apiClient.post('/workbooks/1/zzz')\n"
                                 "apiClient.get(`/workbooks/${id}/extra`);\n"}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        _route_project(root, pages, "app.get('/api/x', h); // router.post('/api/workbooks/:id/undo', u)\n"
                                    "const home = 'http://localhost/'; app.get('/api/workbooks/:id/extra', e);\n")
        found = {(m, p) for m, p, _ in detaudit.unanswered_api_calls(root)}
        assert ("POST", "/api/workbooks/*/undo") in found and ("GET", "/api/workbooks/*/extra") not in found, found
        assert not any("zzz" in p for _, p in found)

@case
def the_v116_undo_redo_shape_is_still_found_with_an_import():
    from agent import detaudit
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "backend" / "src" / "routes").mkdir(parents=True)
        (root / "frontend" / "src" / "pages").mkdir(parents=True)
        (root / "frontend" / "src" / "api").mkdir(parents=True)
        (root / "backend" / "src" / "app.js").write_text(
            "const sheets = require('./routes/sheets');\napp.use('/api/workbooks', require('./routes/workbooks'));\n"
            "app.use('/api/workbooks', sheets);\napp.get('/api/health', h);\n", encoding="utf-8")
        (root / "backend" / "src" / "routes" / "workbooks.js").write_text(
            "router.get('/', a);\nrouter.post('/', b);\nrouter.get('/:id', c);\nrouter.patch('/:id', d);\n", encoding="utf-8")
        (root / "backend" / "src" / "routes" / "sheets.js").write_text("router.post('/:id/sheets', e);\n", encoding="utf-8")
        (root / "frontend" / "src" / "api" / "index.ts").write_text(
            "const apiClient = axios.create({ baseURL: '/api' });\nexport default apiClient;\n", encoding="utf-8")
        (root / "frontend" / "src" / "pages" / "Editor.tsx").write_text(
            "import apiClient from '../api/index';\n"
            "function insert(kind: 'row' | 'col') { return apiClient.post(`/workbooks/${id}/${kind}`, {}); }\n"
            "async function handleUndoRedo(kind: 'undo' | 'redo') {\n  await apiClient.post(`/workbooks/${id}/${kind}`, {});\n}\n"
            "apiClient.get(`/workbooks/${id}`);\napiClient.post('/workbooks', {});\napiClient.post(`/workbooks/${id}/sheets`, {});\n"
            "apiClient.get('/health');\napiClient.get(url);\n", encoding="utf-8")
        found = detaudit.unanswered_api_calls(root)
        assert [(m, p) for m, p, _ in found] == [("POST", "/api/workbooks/*/row"), ("POST", "/api/workbooks/*/col"),
                                                 ("POST", "/api/workbooks/*/undo"), ("POST", "/api/workbooks/*/redo")], found

@case
def the_snapshot_measures_grid_rows_and_opens_a_record_once():
    from agent import audit
    script = audit.SNAPSHOT_SCRIPT
    grid = script[script.index("const GRID_CHECK"):script.index("(async () => {")]
    assert "\\" not in grid
    assert script.count("entry.gridLayout = await GRID_TWICE(page)") == 3
    assert "entry.gridLayout = await page.evaluate(GRID_CHECK)" not in script

    click = script[script.index("if (index >= 0) {"):script.index("entry.clickError")]
    assert "const before = page.url();" in click and "if (page.url() !== before) {" in click
    assert click.index("if (page.url() !== before) {") < click.index("entry.via = via;")
    assert "viewport: { width: 1280, height: 800 }" in script
    assert "else if (entry.gridLayout && entry.gridLayout.found.length) out.push(entry);" in script
    assert "if (entry.via && (entry.console.length || (entry.gridLayout && entry.gridLayout.found.length))) out.push(entry);" in script
    assert "if (!reachedGrid)" in script and "name === (heading.innerText || '').trim() && !risky.test(name)" in script
    assert "delete|remove|trash|archive" in script and "(log|sign)[-_ ]?(out|off)" in script
    assert script.count("entry.relabeled = ") == 2
    node = shutil.which("node")
    if node:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.cjs"
            path.write_text(script, encoding="utf-8")
            done = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=60)
            assert done.returncode == 0, done.stderr[-500:]

@case
def a_grid_finding_counts_only_when_two_measurements_find_it():
    from agent import audit
    node = shutil.which("node")
    if not node:
        return
    script = audit.SNAPSHOT_SCRIPT
    twice = script[script.index("const GRID_TWICE"):script.index("(async () => {")]
    probe = NL.join([
        "const GRID_CHECK = 'check';",
        twice,
        "const f = (grid, problem) => ({ grid, problem });",
        "const fake = (answers) => { const waits = []; const page = {",
        "  evaluate: async () => answers.shift(), waitForTimeout: async (ms) => { waits.push(ms); } }; return { page, waits }; };",
        "(async () => {",
        "  const out = [];",
        "  let t = fake([{ ms: 1, seen: [], found: [f('g', 'stacked'), f('h', 'side')] }, { ms: 2, seen: [1], found: [f('g', 'stacked')] }]);",
        "  let r = await GRID_TWICE(t.page); out.push([r.found.map((x) => x.grid), t.waits, r.measured]);",
        "  t = fake([{ ms: 1, seen: [], found: [f('g', 'stacked')] }, { ms: 1, seen: [], found: [] }]);",
        "  r = await GRID_TWICE(t.page); out.push([r.found.length, t.waits]);",
        "  t = fake([{ ms: 1, seen: [], found: [] }, { ms: 1, seen: [], found: [f('g', 'stacked')] }]);",
        "  r = await GRID_TWICE(t.page); out.push([r.found.length, t.waits]);",
        "  t = fake([{ ms: 1, seen: [], found: [f('g', 'stacked')] }, { ms: 1, seen: [], found: [f('g', 'side')] }]);",
        "  r = await GRID_TWICE(t.page); out.push([r.found.length]);",
        "  console.log(JSON.stringify(out));",
        "})();",
    ])
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "twice.cjs"
        path.write_text(probe, encoding="utf-8")
        done = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=60)
        assert done.returncode == 0, done.stderr[-500:]
        import json

        assert json.loads(done.stdout) == [[["g"], [600], 2], [0, [600]], [0, []], [0]], done.stdout

@case
def a_grid_whose_rows_stack_their_cells_is_a_finding():
    from agent import detaudit

    def layout(stacked: int, rows: int, same: int = 0, pairs: int = 0) -> dict:
        return {"ms": 1, "seen": [{"grid": "Worksheet grid", "rows": rows}], "found": [
            {"grid": "Worksheet grid", "rowsChecked": rows, "stackedRows": stacked, "rowPairs": pairs,
             "sameLineRowPairs": same, "problem": "p", "example": {"row": "A\nB\nC", "cells": []}}]}
    snapshot = [{"route": "/", "gridLayout": {"ms": 0, "seen": [], "found": []}},
                {"route": "/workbooks/1", "crawled": True, "console": [], "gridLayout": layout(31, 31)},
                {"route": "/workbook/1", "crawled": True, "console": [], "via": "Q3 Sales", "gridLayout": layout(16, 16)},
                {"route": "/w/2", "crawled": True, "console": [], "gridLayout": layout(15, 31)},
                {"route": "/w/3", "crawled": True, "console": [], "gridLayout": layout(0, 8, 5, 7)}]
    lines: list[str] = []
    found = [f for f in detaudit.deterministic_findings(Path("."), {"id": "REQ-1"}, snapshot, log=lines.append)
             if "stacked vertically" in f["problem"]]
    assert [f["where"] for f in found] == ["route /workbooks/1", "route /workbook/1"], found
    assert "31 of 31 rows" in found[0]["problem"] and "clicking “Q3 Sales”" in found[1]["problem"]
    assert "horizontal layout (flex or CSS grid" in found[0]["fix"]
    assert any("side by side" in line and "not reported" in line for line in lines), lines
    assert any("grid_layout=2" in line for line in lines)

@case
def a_malformed_scenario_list_is_skipped_and_quote_style_does_not_zero_a_weight():
    import main as factory_module
    odd = {"id": "ROOT", "children": [
        {"id": "REQ-1", "type": "ATOMIC", "scenarios": "not a list"},
        {"id": "REQ-2", "type": "ATOMIC", "scenarios": [{"steps": {"keyword": "GIVEN"}}, "a string", None]},
    ]}
    factory_module.seed_scenarios(odd)
    seeds = ("SEED\n- Workbook `Q3 Sales` holds cells `A1`=“Region” and `B1`=“Total”.\n"
             "- Workbook `Q3 Sales` holds cells `A1`=“Item” and `B1`=“Qty”.")
    factory_module.seed_content_differences(seeds, odd)
    tree = {"id": "ROOT", "children": [{"id": "REQ-1", "type": "ATOMIC", "scenarios": [{"steps": [
        {"keyword": "GIVEN", "content": "Workbook `Q3 Sales` holds cells `A1`=\"Region\" and `B1`=\"Total\""}]}]}]}
    note = factory_module.seed_content_differences(seeds, tree)
    assert "(1 scenario(s)) Workbook `Q3 Sales` holds cells `A1`=“Region”" in note, note
    source = inspect.getsource(factory_module.Factory.run)
    assert "seed-difference note skipped" in source

@case
def a_binding_inside_a_comment_or_string_is_no_binding():
    from agent.codegen import binds_name
    assert not binds_name("// action = handlers[k]\nawait action(page);", "action")
    assert not binds_name("/* const action */ await action(page);", "action")
    assert not binds_name("const m = 'action = 1'; await action(page);", "action")
    assert not binds_name("const m = `let action`; await action(page);", "action")
    assert binds_name("const action = handlers[await s.textContent()];", "action")
    assert binds_name("const { action } = handlers;", "action")
    assert not binds_name("const { action } = require('./x');", "action")
    assert not binds_name("const helpers = require('./h');\nconst { action } = helpers;", "action")
    assert binds_name("const helpers = pick();\nconst { action } = helpers;", "action")

@case
def the_platform_cases_of_section_78_are_caught():
    from agent.testrun import cells_as_items, outsider_undefined_calls
    rel = "backend/test-e2e/REQ-3-1-3.spec.js"

    cells = 'await see(page, "Item", item(page, "D1"));\nawait see(page, "4", item(page, "E2"));\n'
    assert len(cells_as_items(rel, cells)) == 2, cells_as_items(rel, cells)
    assert cells_as_items(rel, "await click(page, 'Delete', item(page, 'Q3 Sales'));") == []
    assert cells_as_items(rel, 'await click(page, "Open", item(page, "PR1"));') == []
    assert cells_as_items(rel, 'await click(page, "Open", item(page, "Q3"));') == []
    assert cells_as_items(rel, 'await see(page, "x", item(page, { role: "gridcell", name: "B2" }));')
    assert cells_as_items("backend/tests/x.test.js", cells) == []

    spec = ('const { test, goto } = require("@playwright/test");\n'
            'const { expect, click, fill, check, press, see, notSee, seeOnce, locate, freshSession } = require("../e2e-support/outsider");\n'
            'test("a", async ({ page }) => { await goto(page, "/"); });\n')
    found = [p for p in outsider_undefined_calls("backend/test-e2e/REQ-1-1-3.spec.js", spec) if "@playwright/test" in p]
    assert found and "goto" in found[0], found
    esm = ('import { test, goto } from "@playwright/test";\nconst { see } = require("../e2e-support/outsider");\n'
           'test("a", async ({ page }) => { await goto(page, "/"); });\n')
    assert [p for p in outsider_undefined_calls("backend/test-e2e/B.spec.js", esm) if "@playwright/test" in p and "goto" in p]
    ok = 'const { test, expect } = require("@playwright/test");\ntest("a", async ({ page }) => { await page.goto("/"); });\n'
    assert not [p for p in outsider_undefined_calls("backend/test-e2e/A.spec.js", ok) if "@playwright/test" in p]

    from agent.testrun import static_test_defects
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "backend" / "test-e2e").mkdir(parents=True)
        (Path(tmp) / "backend" / "test-e2e" / "REQ-3-1-3.spec.js").write_text(
            'const { test } = require("@playwright/test");\nconst { see, item } = require("../e2e-support/outsider");\n'
            'test("a", async ({ page }) => {\n' + cells + "});\n", encoding="utf-8")
        report = static_test_defects(Path(tmp), ["backend/test-e2e/REQ-3-1-3.spec.js"])
        assert 'item(page, ...) with the cell "D1"' in report, report

@case
def the_fixes_of_audit_round_38_hold():
    import main as factory_module
    from agent import outsider
    from agent.detaudit import unique_scope

    both = {"id": "R", "children": [
        {"id": "A", "type": "ATOMIC", "description": "", "scenarios": [{"steps": [
            {"keyword": "GIVEN", "content": "The target team name `n-new` does not exist."}]}]},
        {"id": "B", "type": "ATOMIC", "description": "", "scenarios": [{"steps": [
            {"keyword": "GIVEN", "content": "Seed values: team `n-new`."}]}]}]}
    lines: list[str] = []
    assert "`n-new`" not in factory_module.seed_constraints_note(both, log=lines.append)
    assert any("left out" in line and "n-new" in line for line in lines), lines

    filler = [{"id": f"F{i}", "type": "ATOMIC", "description": "", "scenarios": [{"steps": [
        {"keyword": "GIVEN", "content": f"Seed values: record `filler-{i}-" + "x" * 60 + "`."}]}]} for i in range(120)]
    late = {"id": "R", "children": [both["children"][0]] + filler + [both["children"][1]]}
    assert "`n-new`" not in factory_module.seed_constraints_note(late)

    dq = {"id": "R", "children": [both["children"][0], {"id": "B", "type": "ATOMIC", "description": "", "scenarios": [
        {"steps": [{"keyword": "GIVEN", "content": 'Seed values: team "n-new".'}]}]}]}
    if factory_module.seed_digest(dq):
        assert "`n-new`" not in factory_module.seed_constraints_note(dq)

    scope, quoted = unique_scope("Signed-in scenarios begin with the unique Sign in link on the home page, labeled fields.")
    assert scope.get(("link", "Sign in")) == "home" and ("link", "Sign in") not in quoted
    scope, quoted = unique_scope("The page contains exactly one button named “Account menu”.")
    assert scope.get(("button", "Account menu")) == "" and ("button", "Account menu") in quoted

    source = inspect.getsource(outsider)
    assert "extract_stated_controls(text, unquoted=False)" in source

    body = outsider.HELPER_JS.split("async function drag(")[1].split("\nfunction ")[0]
    assert body.index("page.mouse.down()") < body.index("scrollIntoViewIfNeeded") < body.index("for (let i = 1; i <= DRAG_STEPS")

@case
def an_unquoted_current_name_quoted_later_is_not_refused():
    import main as factory_module
    from agent import outsider
    work = [{"id": "REQ-A", "type": "ATOMIC", "description": "Compare changes is a button on the comparison page."},
            {"id": "REQ-B", "type": "ATOMIC", "description": "The user clicks “Compare changes” again."}]
    factory_module.stamp_batch_names(work)
    assert "Compare changes" not in work[0]["later_names"], work[0]
    spec = ('const { click } = require("../e2e-support/outsider");\n'
            'test("REQ-A", async ({ page }) => { await click(page, { role: "button", name: "Compare changes" }); });\n')
    assert outsider.later_controls(spec, work[0]) == []

    work2 = [{"id": "REQ-A", "type": "ATOMIC", "description": "The page opens."},
             {"id": "REQ-B", "type": "ATOMIC", "description": "The user clicks “Compare changes”."}]
    factory_module.stamp_batch_names(work2)
    assert outsider.later_controls(spec, work2[0]) == ["Compare changes"]

    work3 = [{"id": "REQ-A", "type": "ATOMIC", "description": "The page opens."},
             {"id": "REQ-B", "type": "ATOMIC", "description": "Compare changes is a button."}]
    factory_module.stamp_batch_names(work3)
    assert "Compare changes" not in work3[0]["later_names"] and outsider.later_controls(spec, work3[0]) == []

@case
def a_rewritten_spec_goes_through_the_scripted_review_once_more():
    import main as factory_module
    from agent import outsider
    from agent.test_author import AuthoredFile
    first = AuthoredFile(path="backend/test-e2e/REQ-1.spec.js", content="rewrite-1")
    second = AuthoredFile(path="backend/test-e2e/REQ-1.spec.js", content="rewrite-2")
    calls = {"author": 0, "review": []}
    saved = (outsider.deterministic_review, factory_module.author_acceptance)
    try:
        def review(node, spec, prefix, log=print):
            calls["review"].append(spec.content)
            return ["PROBLEM: ROLE | bare name"] if spec.content == "rewrite-1" else []

        def author(llm, node, prefix, problem="", current="", log=print):
            calls["author"] += 1
            assert "PROBLEM: ROLE" in problem and current == "rewrite-1"
            return second
        outsider.deterministic_review, factory_module.author_acceptance = review, author
        factory = factory_module.Factory.__new__(factory_module.Factory)
        factory.llm = None
        assert factory.recheck_rewritten_spec({"id": "REQ-1"}, "REQ-1", first) is second and calls["author"] == 1
        assert calls["review"] == ["rewrite-1", "rewrite-2"]
        calls["author"], calls["review"] = 0, []
        assert factory.recheck_rewritten_spec({"id": "REQ-1"}, "REQ-1", second) is second and calls["author"] == 0

        def broken(llm, node, prefix, problem="", current="", log=print):
            raise RuntimeError("gateway")
        factory_module.author_acceptance = broken
        assert factory.recheck_rewritten_spec({"id": "REQ-1"}, "REQ-1", first) is first
    finally:
        outsider.deterministic_review, factory_module.author_acceptance = saved
    source = inspect.getsource(factory_module.Factory.repair_tests) + inspect.getsource(factory_module.Factory.author_tests)
    assert source.count("self.recheck_rewritten_spec(") == 2

@case
def the_fixes_of_audit_round_41_hold():
    from agent import audit, contract
    flat = " ".join(contract.ARCHITECT_PROMPT.split()) if hasattr(contract, "ARCHITECT_PROMPT") else " ".join(
        inspect.getsource(contract).split())

    assert "uniqueness is then per container, and a name the requirement quotes is never rewritten" in flat

    script = audit.SNAPSHOT_SCRIPT
    assert "el.closest('[aria-hidden=\"true\"],[inert]')" in script
    assert "data-state') === 'closed'" in script and "[aria-expanded=\"false\"]" in script

@case
def the_log_census_conflicts_of_section_91_are_resolved():
    import json as json_module
    import main as factory_module
    from agent import outsider
    from agent.codegen import spec_evidence

    node = {"id": "REQ-1", "later_names": ["A1", "Formula bar"], "seed_sentences": ""}
    spec = 'await click(page, { role: "gridcell", name: "A1" });\nawait fill(page, "Formula bar", "=1");\n'
    assert outsider.later_controls(spec, node) == ["Formula bar"]

    requirement = "Each scenario starts with the account's own verified email in the stated credential state."
    tests = {"backend/test-e2e/REQ-1-3.spec.js": 'await signIn(page, "alice-dev", "Valid-password-123!");'}

    claim = ('SPEC: the requirement says "Each scenario starts with the account’s own verified email", yet the '
             'spec signs in as “alice-dev”')
    assert spec_evidence(claim, requirement, tests, "")

    contract = json_module.dumps({"notes": ["keep"]})
    note = "SEED RECORDS WITH DIFFERENT CONTENTS (decided from the requirement text):\n`X` is given different contents"
    once = factory_module.contract_with_seed_constraints(contract, note, head="SEED RECORDS WITH DIFFERENT CONTENTS")
    twice = factory_module.contract_with_seed_constraints(once, note, head="SEED RECORDS WITH DIFFERENT CONTENTS")
    assert json_module.loads(twice)["notes"] == ["keep", note]
    source = inspect.getsource(factory_module.Factory.run)
    assert 'contract_with_seed_constraints(self.contract, differences' in source

@case
def the_conventions_of_section_86_are_present():
    from agent.conventions import PROJECT_CONVENTIONS as text
    flat = " ".join(text.split())
    assert "seed supplies SEPARATE records for different outcomes" in flat
    assert "the eligible one meets every condition the requirement lists for success" in flat
    assert "is owned by THAT owner in the data model" in flat
    assert "an empty input is never silently turned into zero" in flat
    for literal in ("Q3 Sales", "alice-dev", "acme", "Improve onboarding", "AVERAGE"):
        assert literal not in text.split("ADR-099 section 86")[0][-900:], literal

@case
def the_shared_sweep_keeps_a_margin_a_cap_and_can_be_switched_off():
    import main as factory_module
    from agent import settings as settings_module
    from agent.verify import run_e2e_in_order
    assert factory_module.Factory.SHARED_SWEEP_MARGIN_SEC >= 900 and factory_module.Factory.SHARED_SWEEP_CAP_SEC <= 1200
    source = inspect.getsource(factory_module.Factory.shared_context_sweep)
    assert "- self.SHARED_SWEEP_MARGIN_SEC" in source and "self.SHARED_SWEEP_CAP_SEC" in source
    assert "shared_sweep" in settings_module._DEFAULTS
    ordered = inspect.getsource(run_e2e_in_order)
    assert ordered.index("try:") < ordered.index("env = fresh_data_env(project_dir)") < ordered.index("finally:")
    assert ordered.index("try:") < ordered.index("server = BackgroundProcess(")

@case
def a_spec_defect_the_helper_reports_goes_to_the_spec():
    from agent.codegen import spec_thrown_errors
    output = "\n".join([
        "E2E FAILED against the real application",
        "  1) test-e2e/REQ-1-1-2.spec.js:9:1 › sign in ─────",
        '    Error: SPEC DEFECT: "btn" is no ARIA role -- use one (button, link, textbox, checkbox, ...) or the plain name the requirement quotes (ADR-099).',
        "        at byRole (C:\\work\\backend\\e2e-support\\outsider.js:197:13)",
        "        at click (C:\\work\\backend\\e2e-support\\outsider.js:260:5)",
        "        at C:\\work\\backend\\test-e2e\\REQ-1-1-2.spec.js:14:9",
    ])
    spec = {"backend/test-e2e/REQ-1-1-2.spec.js": "click(page, { role: 'btn', name: 'Sign in' })"}
    found = spec_thrown_errors(output, spec)
    assert found and found[0].startswith("REQ-1-1-2.spec.js: Error: SPEC DEFECT"), found
    assert spec_thrown_errors(output, {"backend/test-e2e/OTHER.spec.js": ""}) == []

@case
def a_spec_verdict_reaches_the_spec_even_when_the_unit_test_is_named():
    import main as factory_module
    from agent.test_author import AuthoredFile
    calls = {"spec": 0, "others": []}

    class Author:
        def repair(self, node, prefix, files, problem, contract=""):
            calls["others"] = [f.path for f in files]
            return files

    original = factory_module.author_acceptance
    try:
        def fake_acceptance(llm, node, prefix, problem="", current="", log=print):
            calls["spec"] += 1
            return AuthoredFile(path="backend/test-e2e/REQ-1-3.spec.js", content=current + "\n// revised")
        factory_module.author_acceptance = fake_acceptance
        factory = factory_module.Factory.__new__(factory_module.Factory)
        factory.author, factory.llm = Author(), None
        spec = AuthoredFile(path="backend/test-e2e/REQ-1-3.spec.js", content='require("../e2e-support/outsider");')
        unit = AuthoredFile(path="backend/tests/REQ-1-3.test.js", content="unit")
        problem = ("The acceptance spec contradicts the requirement text:\nSPEC: Requirement REQ-1-3 states ..., yet the "
                   "spec runs ...\n(advisory API tests failing: backend/tests/REQ-1-3.test.js)")
        out = factory.repair_tests({"id": "REQ-1-3"}, "REQ-1-3", [unit, spec], problem)
        assert calls["spec"] == 1, calls
        assert any(f.path.endswith(".spec.js") and f.content.endswith("// revised") for f in out)
        calls["spec"] = 0
        factory.repair_tests({"id": "REQ-1-3"}, "REQ-1-3", [unit, spec], "backend/tests/REQ-1-3.test.js: expected 201, got 400")
        assert calls["spec"] == 0
        factory.repair_tests({"id": "REQ-1-3"}, "REQ-1-3", [unit, spec],
                             "backend/tests/REQ-1-3.test.js: the handler the acceptance spec relies on returns 400")
        assert calls["spec"] == 0
    finally:
        factory_module.author_acceptance = original

@case
def gate_e1_looks_again_after_a_test_defect_repair():
    import main as factory_module
    source = inspect.getsource(factory_module.Factory.process_node)
    loop = source.index('while result.status == "test_defect"')

    repair = source.index("files, test_files = self.repair_tests_within(req_id, node, prefix, files, test_files, result.detail", loop)
    recheck = source.index("rewritten = static_test_defects(self.project_dir, list(test_files))", repair)
    assert recheck < source.index("first = node_check()", repair)

@case
def a_seed_value_counts_only_as_a_whole_token():
    import main as factory_module
    seeds = "SEED\n- Workbook `Q3 Sales` holds `East` with `700` in B2.\n"
    before = {"backend/src/seed.js": b"rows = [['East', 700]]", "frontend/src/A.tsx": b"<p className='text-gray-700'/>"}
    after = {"backend/src/seed.js": b"rows = [['East', 1200]]", "frontend/src/A.tsx": b"<p className='text-gray-700'/>"}
    assert factory_module.lost_seed_values(seeds, before, after) == ["700"]

@case
def the_seed_note_forbids_whole_line_equalities():
    import main as factory_module
    seeds = ("SEED\n- Workbook `Q3 Sales` holds cells `A1`=`Region` and `B1`=`Sales`.\n"
             "- Workbook `Q3 Sales` holds cells `C1`=`Status`.")
    note = factory_module.seed_content_differences(seeds, None)
    assert "holds ONLY those values" in note and "<a>,<b>,<c>" in note
    assert "states the WHOLE output exactly" in note
    assert "East" not in note

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
            traceback.print_exc(limit=3)
    print(f"{len(CASES) - failed}/{len(CASES)} audit cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
