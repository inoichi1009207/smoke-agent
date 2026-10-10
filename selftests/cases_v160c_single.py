from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.single_source import Finding, audit, render

CASES = []

APP_HEAD = "const express = require('express');\nconst app = express();\n"
ROUTER_HEAD = "const express = require('express');\nconst router = express.Router();\n"
SCHEMA = """const SCHEMA = `
CREATE TABLE IF NOT EXISTS organizations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS repositories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_type TEXT NOT NULL,
  owner_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  UNIQUE(owner_type, owner_id, name)
);
CREATE TABLE IF NOT EXISTS sheets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workbook_id INTEGER NOT NULL REFERENCES workbooks(id),
  name TEXT NOT NULL
);
`;
module.exports = { SCHEMA };
"""
ENGINE = """// formula engine
function tokenize(text) {
  const out = [];
  const re = /\\$?([A-Z]+)\\$?(\\d+)/y;
  for (const ch of text) { if ('+-*/'.includes(ch)) out.push(ch); }
  return out;
}
function parseExpression(tokens) { return tokens.length; }
function evaluateFormula(raw, cells) {
  if (!raw.startsWith('=')) return raw;
  return parseExpression(tokenize(raw.slice(1)));
}
module.exports = { evaluateFormula };
"""
ENGINE_TS = ENGINE.replace("module.exports = { evaluateFormula };", "export { evaluateFormula };")

def case(function):
    CASES.append(function)
    return function

def project(files: dict[str, str]) -> tuple[tempfile.TemporaryDirectory, Path]:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    (root / "backend" / "src").mkdir(parents=True)
    (root / "frontend" / "src").mkdir(parents=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp, root

def run(files: dict[str, str]) -> list[Finding]:
    tmp, root = project(files)
    try:
        return audit(root)
    finally:
        tmp.cleanup()

def kinds(findings: list[Finding], kind: str) -> list[Finding]:
    return [f for f in findings if f.kind == kind]

def route_file(*lines: str) -> str:
    return ROUTER_HEAD + "\n".join(lines) + "\nmodule.exports = router;\n"

@case
def double_mount_same_router_twice():
    found = run({
        "backend/src/app.js": APP_HEAD + "const collab = require('./routes/collab');\n"
        + "app.use('/api/repos', collab);\napp.use('/api', collab);\nmodule.exports = app;\n",
        "backend/src/routes/collab.js": route_file("router.get('/:o/:r/collaborators', (req, res) => res.json([]));"),
    })
    mounts = kinds(found, "double-mount")
    assert len(mounts) == 1, found
    assert mounts[0].files == ["backend/src/app.js", "backend/src/app.js"] and mounts[0].lines == [4, 5], mounts
    assert "mount each router once" in mounts[0].message.lower(), mounts[0].message

@case
def double_mount_inline_require_counts_too():
    found = run({
        "backend/src/app.js": APP_HEAD + "const a = require('./routes/a');\napp.use('/api', a);\n"
        + "app.use('/v1', require('./routes/a'));\n",
        "backend/src/routes/a.js": route_file("router.get('/a', (req, res) => res.json([]));"),
    })
    assert len(kinds(found, "double-mount")) == 1, found

@case
def two_different_routers_on_one_prefix_is_fine():
    found = run({
        "backend/src/app.js": APP_HEAD + "const a = require('./routes/a');\nconst b = require('./routes/b');\n"
        + "app.use('/api', a);\napp.use('/api', b);\n",
        "backend/src/routes/a.js": route_file("router.get('/a', (req, res) => res.json([]));"),
        "backend/src/routes/b.js": route_file("router.get('/b', (req, res) => res.json([]));"),
    })
    assert found == [], found

@case
def two_routers_exported_by_one_module_are_two_routers():
    two = ("const express = require('express');\nconst publicRouter = express.Router();\n"
           "const adminRouter = express.Router();\npublicRouter.get('/p', (req, res) => res.json(1));\n"
           "adminRouter.get('/a', (req, res) => res.json(1));\nmodule.exports = { publicRouter, adminRouter };\n")
    found = run({
        "backend/src/app.js": APP_HEAD + "const { publicRouter, adminRouter } = require('./routes/two');\n"
        + "app.use('/api', publicRouter);\napp.use('/api/admin', adminRouter);\n",
        "backend/src/routes/two.js": two,
    })
    assert found == [], found

def shadow_project(lines: list[str]) -> list[Finding]:
    return run({
        "backend/src/app.js": APP_HEAD + "const x = require('./routes/x');\napp.use('/api', x);\n",
        "backend/src/routes/x.js": route_file(*lines),
    })

@case
def param_route_before_literal_shadows_it():
    found = shadow_project(["router.get('/x/:id', (req, res) => res.json({ id: req.params.id }));",
                            "router.get('/x/new', (req, res) => res.json({ form: true }));"])
    shadowed = kinds(found, "shadowed-route")
    assert len(shadowed) == 1, found
    f = shadowed[0]
    assert f.files == ["backend/src/routes/x.js", "backend/src/routes/x.js"] and f.lines == [4, 3], f
    assert "GET /api/x/new" in f.message and "never runs" in f.message and "probably" not in f.message, f.message

@case
def literal_route_before_param_is_fine():
    found = shadow_project(["router.get('/x/new', (req, res) => res.json({ form: true }));",
                            "router.get('/x/:id', (req, res) => res.json({ id: req.params.id }));"])
    assert found == [], found

@case
def different_method_or_segment_count_is_fine():
    found = shadow_project(["router.get('/x/:id', (req, res) => res.json(1));",
                            "router.post('/x/new', (req, res) => res.json(2));",
                            "router.get('/x/new/edit', (req, res) => res.json(3));"])
    assert found == [], found

@case
def identical_pattern_in_two_routers_shadows_the_later_one():
    found = run({
        "backend/src/app.js": APP_HEAD + "const code = require('./routes/code');\nconst repos = require('./routes/repos');\n"
        + "app.use('/api', code);\napp.use('/api', repos);\n",
        "backend/src/routes/code.js": ROUTER_HEAD + "async function commitsHandler(req, res, next) {\n"
        + "  try { res.json([]); } catch (err) { next(err); }\n}\n"
        + "router.get('/repos/:owner/:repo/commits', commitsHandler);\nmodule.exports = router;\n",
        "backend/src/routes/repos.js": route_file("router.get('/repos/:owner/:repo/commits', async (req, res, next) => {",
                                                  "  res.json([]);", "});"),
    })
    shadowed = kinds(found, "shadowed-route")
    assert len(shadowed) == 1 and shadowed[0].files[0] == "backend/src/routes/repos.js", found
    assert "same pattern" in shadowed[0].message, shadowed[0].message

@case
def earlier_handler_that_calls_next_is_a_fall_through():
    found = shadow_project(["router.get('/repos/:owner/:repo', (req, res, next) => {",
                            "  if (req.params.repo === 'pulls') { next(); return; }",
                            "  res.json({});",
                            "});",
                            "router.get('/repos/:name/pulls', (req, res) => res.json([]));"])
    assert found == [], found

@case
def imported_handler_is_only_probably_shadowing():
    found = run({
        "backend/src/app.js": APP_HEAD + "const x = require('./routes/x');\napp.use('/api', x);\n",
        "backend/src/routes/x.js": ROUTER_HEAD + "const { show } = require('./handlers');\n"
        + "router.get('/x/:id', show);\nrouter.get('/x/new', (req, res) => res.json(1));\nmodule.exports = router;\n",
        "backend/src/routes/handlers.js": "function show(req, res) { res.json(1); }\nmodule.exports = { show };\n",
    })
    shadowed = kinds(found, "shadowed-route")
    assert len(shadowed) == 1 and "probably" in shadowed[0].message, found

@case
def regex_paths_are_skipped():
    found = shadow_project(["router.get(/^\\/x\\/.*/, (req, res) => res.json(1));",
                            "router.get('/x/new', (req, res) => res.json(2));"])
    assert found == [], found

@case
def double_mounted_router_is_not_also_reported_as_shadowing_itself():
    found = run({
        "backend/src/app.js": APP_HEAD + "const a = require('./routes/a');\napp.use('/api', a);\napp.use('/api', a);\n",
        "backend/src/routes/a.js": route_file("router.get('/a/:id', (req, res) => res.json([]));"),
    })
    assert [f.kind for f in found] == ["double-mount"], found

def lookup_project(helper_sql: str, extra: dict[str, str] | None = None) -> list[Finding]:
    files = {
        "backend/src/database/init_db.js": SCHEMA,
        "backend/src/routes/repo_helpers.js": "const { get } = require('../database/db');\n"
        + "async function loadRepo(owner, name) {\n  return get(" + helper_sql + ", [name]);\n}\n"
        + "module.exports = { loadRepo };\n",
    }
    files.update(extra or {})
    return run(files)

@case
def name_only_select_on_an_owned_table_is_reported():
    found = lookup_project("'SELECT * FROM repositories WHERE lower(name) = lower(?) ORDER BY id LIMIT 1'")
    hits = kinds(found, "name-only-lookup")
    assert len(hits) == 1 and hits[0].files == ["backend/src/routes/repo_helpers.js"] and hits[0].lines == [3], found
    assert "owner AND name" in hits[0].message and "owner_id" in hits[0].message, hits[0].message

@case
def owner_type_alone_does_not_scope():
    found = lookup_project("\"SELECT * FROM repositories WHERE owner_type = 'org' AND name = ?\"")
    assert len(kinds(found, "name-only-lookup")) == 1, found

@case
def owner_and_name_is_fine():
    assert lookup_project("'SELECT * FROM repositories WHERE owner_type = ? AND owner_id = ? AND name = ?'") == []
    joined = ("`SELECT r.* FROM repositories r JOIN organizations o ON o.id = r.owner_id "
              "WHERE o.name = ? AND r.name = ?`")
    assert lookup_project(joined) == []

@case
def owner_column_without_unique_scopes_too():
    found = lookup_project("'SELECT 1'", {
        "backend/src/routes/sheets.js": "const { get } = require('../database/db');\n"
        + "async function sheetByName(name) {\n  return get('SELECT * FROM sheets WHERE name = ?', [name]);\n}\n"})
    hits = kinds(found, "name-only-lookup")
    assert len(hits) == 1 and hits[0].files == ["backend/src/routes/sheets.js"] and "workbook_id" in hits[0].message, found

@case
def globally_unique_names_search_and_id_lookups_are_fine():
    assert lookup_project("'SELECT * FROM organizations WHERE name = ?'") == []
    assert lookup_project("'SELECT * FROM repositories WHERE name LIKE ?'") == []
    assert lookup_project("'SELECT * FROM repositories WHERE id = ? AND name = ?'") == []

@case
def built_or_nested_sql_is_not_read():
    assert lookup_project("'SELECT * FROM repositories WHERE name = ?' + (owner ? ' AND owner_id = ?' : '')") == []
    assert lookup_project("`SELECT * FROM repositories WHERE name = ? ${ownerClause}`") == []
    nested = ("'SELECT * FROM repositories WHERE name = ? AND owner_id IN (SELECT id FROM organizations WHERE "
              "name = ?)'")
    assert lookup_project(nested) == []

@case
def several_lookup_helpers_in_several_modules():
    other = ("const { get } = require('../database/db');\nasync function findRepo(owner, name) {\n"
             "  return get('SELECT * FROM repositories WHERE owner_id = ? AND name = ?', [owner, name]);\n}\n")
    found = lookup_project("'SELECT * FROM repositories WHERE name = ?'", {"backend/src/routes/issues.js": other})
    owners = kinds(found, "lookup-owners")
    assert len(owners) == 1, found
    assert sorted(owners[0].files) == ["backend/src/routes/issues.js", "backend/src/routes/repo_helpers.js"], owners
    assert "findRepo" in owners[0].message and "loadRepo" in owners[0].message, owners[0].message

@case
def one_lookup_module_or_all_scoped_is_fine():
    scoped = "'SELECT * FROM repositories WHERE owner_id = ? AND name = ?'"
    other = ("const { get } = require('../database/db');\nasync function findRepo(owner, name) {\n"
             "  return get(" + scoped + ", [owner, name]);\n}\n")
    found = lookup_project(scoped, {"backend/src/routes/issues.js": other})
    assert found == [], found
    handler = ("const { get } = require('../database/db');\nrouter.get('/r/:n', async (req, res) => {\n"
               "  res.json(await get('SELECT * FROM repositories WHERE owner_id = ? AND name = ?', [1, req.params.n]));\n});\n")
    found = lookup_project("'SELECT * FROM repositories WHERE name = ?'", {"backend/src/routes/r.js": handler})
    assert kinds(found, "lookup-owners") == [], found

@case
def two_formula_engines_are_reported():
    found = run({"backend/src/lib/formula.js": ENGINE, "frontend/src/lib/formula.ts": ENGINE_TS})
    engines = kinds(found, "duplicate-engine")
    assert len(engines) == 1, found
    assert engines[0].files == ["backend/src/lib/formula.js", "frontend/src/lib/formula.ts"], engines
    assert engines[0].message.startswith("Advisory") and "One module owns formula evaluation" in engines[0].message

@case
def one_engine_imported_by_the_other_module_is_fine():
    caller = ("import { evaluateFormula } from './formula';\nexport function evaluateCell(raw: string) {\n"
              "  return evaluateFormula(raw, {});\n}\n")
    found = run({"frontend/src/lib/formula.ts": ENGINE_TS, "frontend/src/lib/cells.ts": caller,
                 "backend/src/lib/coords.js": "function colOf(c) { return /^([A-Z]+)(\\d+)$/.exec(c); }\n"
                 + "module.exports = { colOf };\n"})
    assert found == [], found

@case
def same_exported_helper_in_two_modules():
    helper = "function computeHiddenRows(id) {\n  return [id];\n}\nmodule.exports = { computeHiddenRows };\n"
    found = run({"backend/src/lib/detail.js": helper,
                 "backend/src/routes/data.js": helper.replace("module.exports = {", "module.exports = { other: 1,")})
    dups = kinds(found, "duplicate-helper")
    assert len(dups) == 1 and dups[0].lines == [1, 1] and "computeHiddenRows" in dups[0].message, found

@case
def reexported_or_private_helpers_are_fine():
    found = run({
        "backend/src/lib/detail.js": "function rows(id) { return [id]; }\nmodule.exports = { rows };\n",
        "backend/src/lib/index.js": "const { rows } = require('./detail');\nmodule.exports = { rows };\n",
        "backend/src/routes/a.js": "function local(id) { return id; }\nmodule.exports = {};\n",
        "backend/src/routes/b.js": "function local(id) { return id; }\nmodule.exports = {};\n",
    })
    assert found == [], found

@case
def tests_and_node_modules_are_skipped():
    helper = "function same(id) { return id; }\nmodule.exports = { same };\n"
    found = run({"backend/src/a.js": helper, "backend/src/a.test.js": helper, "backend/src/tests/b.js": helper,
                 "backend/src/node_modules/x/c.js": helper, "backend/src/database/test_harness.js": helper})
    assert found == [], found

@case
def empty_project_and_missing_dirs():
    assert run({}) == []
    with tempfile.TemporaryDirectory() as tmp:
        assert audit(Path(tmp)) == []
        assert audit(Path(tmp) / "missing") == []
    assert render([]) == ""

@case
def odd_files_never_raise():
    found = run({
        "backend/src/app.js": APP_HEAD + "app.use('/api', require('./routes/x'));\napp.get('/a', (req, res) => {{{\n",
        "backend/src/routes/x.js": "const router = require('express').Router(;\n`unterminated ${ template\n",
        "backend/src/bin.js": "\x00\x01\xff\xfe garbage ' \" ` / ( [ {",
        "frontend/src/App.tsx": "export default function App() { return <p>Don't / stop</p>; }\n",
        "backend/src/database/init_db.js": "CREATE TABLE IF NOT EXISTS broken (name TEXT, owner_id",
    })
    assert isinstance(found, list), found

@case
def render_is_compact_and_cut():
    many = [Finding("shadowed-route", ["backend/src/a.js"], [i], "x" * 200) for i in range(50)]
    text = render(many, limit_chars=1000)
    assert len(text) <= 1100 and "more finding(s) not shown" in text, text
    one = render([Finding("double-mount", ["backend/src/app.js"], [3], "Mount each router once.")])
    assert one.startswith("Single-source audit: 1 finding(s).") and "[double-mount] Mount each router once." in one

@case
def starter_template_reports_nothing():
    template = ROOT / "template"
    if (template / "backend" / "src").is_dir():
        assert audit(template) == [], render(audit(template))

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
            traceback.print_exc(limit=4)
    print(f"{len(CASES) - failed}/{len(CASES)} v160c single-source cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
