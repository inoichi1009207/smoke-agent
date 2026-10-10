from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES = []

def case(function):
    CASES.append(function)
    return function

ROUTER_HEAD = "const express = require('express');\nconst { all, get } = require('../database/db_runtime');\n\nconst router = express.Router();\n\n"
ROUTER_TAIL = "\nmodule.exports = router;\n"

def _project(root: Path, routes: dict[str, str], pages: dict[str, str]) -> Path:
    (root / "backend" / "src" / "routes").mkdir(parents=True)
    (root / "frontend" / "src" / "api").mkdir(parents=True)
    mounts = "".join(f"app.use('/api', require('./routes/{name}'));\n" for name in routes)
    (root / "backend" / "src" / "app.js").write_text(
        "const express = require('express');\nconst app = express();\n" + mounts
        + "app.get(/^(?!\\/api(?:\\/|$)).*/, (req, res) => {\n  res.sendFile('index.html');\n});\nmodule.exports = app;\n",
        encoding="utf-8")
    for name, body in routes.items():
        (root / "backend" / "src" / "routes" / f"{name}.js").write_text(ROUTER_HEAD + body + ROUTER_TAIL, encoding="utf-8")
    template_api = ROOT / "template" / "frontend" / "src" / "api"
    for name in ("index.ts", "store.ts"):
        (root / "frontend" / "src" / "api" / name).write_text(template_api.joinpath(name).read_text(encoding="utf-8"), encoding="utf-8")
    for rel, text in pages.items():
        (root / "frontend" / "src" / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / "frontend" / "src" / rel).write_text(text, encoding="utf-8")
    return root

def _mismatches(routes: dict[str, str], pages: dict[str, str]):
    from agent.response_shapes import response_shape_mismatches
    with tempfile.TemporaryDirectory() as tmp:
        why: list[str] = []
        found = response_shape_mismatches(_project(Path(tmp), routes, pages), why)
        return found, why

V124_BRANCHES_ROUTE = """// Branch list of a repository (access-checked). The answer is a top-level
// array of { name, head_commit_id } -- the shape the API contract and the
// clients (branchListOf) read.
router.get('/repos/:owner/:repo/branches', async (req, res, next) => {
  try {
    const repo = await findRepo(req.params.owner, req.params.repo);
    if (!repo) {
      res.status(404).json({ error: 'Repository not found' });
      return;
    }
    const user = await currentUser(req);
    if (!(await canReadRepo(user, repo))) {
      res.status(user ? 403 : 401).json({ error: user ? 'Access not permitted' : 'Not signed in' });
      return;
    }
    const rows = await all('SELECT name, head_commit_id FROM branches WHERE repo_id = ? ORDER BY name', [repo.id]);
    const branches = rows.map((row) => ({ name: row.name, head_commit_id: row.head_commit_id || null }));
    res.json(branches);
  } catch (err) {
    console.error(err);
    next(err);
  }
});
"""

V124_FILE_PAGE = """import { useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useData } from '../api/store';

interface TreeData {
  file: { path: string; content: string } | null;
}

interface BranchesData {
  branches: { name: string }[];
}

export default function FilePage() {
  const params = useParams<{ owner: string; repo: string; branch?: string; '*': string }>();
  const { owner, repo } = params;
  const branchesPath = `/repos/${owner}/${repo}/branches`;
  ensureSync(branchesPath);
  const branchesData = useData<BranchesData>(branchesPath);

  const branchNames = (branchesData?.branches ?? []).map((b) => b.name).sort((a, b) => b.length - a.length);
  return null;
}
"""

V124_BRANCH_SELECTOR = """import { useData } from '../api/store';

function branchListOf(data: unknown): { name: string }[] {
  if (data && typeof data === 'object' && Array.isArray((data as { branches?: unknown }).branches)) {
    return (data as { branches: { name: string }[] }).branches;
  }
  return Array.isArray(data) ? (data as { name: string }[]) : [];
}

export default function BranchSelector({ owner, repo }: { owner: string; repo: string }) {
  const branchesPath = `/repos/${owner}/${repo}/branches`;
  const data = useData<BranchesData>(branchesPath);
  const branches = branchListOf(data);
  return branches.length;
}
"""

V103_ME_ROUTE = """router.get('/me', async (req, res) => {
  try {
    const user = await sessionUser(req);
    if (!user) return res.status(401).json({ error: 'Not signed in' });
    res.json({ user: publicUser(user) });
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: 'Internal server error' });
  }
});
"""

V103_APP = """import { peek, preload, put } from './api/store';

type User = { id: number; username: string; email: string };

preload(['/me', '/organizations', '/repositories/public', '/pulls']);

// preload() caches the raw response envelope ({ user: ... }); normalize it to the shape
// login puts and the pages read, so a reload still shows the signed-in user at once.
const preloaded = peek<{ user: User } | null>('/me');
if (preloaded && preloaded.user) {
  put('/me', preloaded.user);
}
"""
V103_LAYOUT = """import { loadNow, put, reset, useData } from '../api/store';
import apiClient from '../api';

type User = { id: number; username: string; email: string };

function Layout({ children }: { children: ReactNode }) {
  const me = useData<User | null>('/me');
  return (
    <div>
              <button
                className="rounded border border-gray-300 px-3 py-1 text-sm font-medium"
              >
                {me.username}
              </button>
    </div>
  );
}
"""

@case
def a_middleware_that_wraps_the_response_silences_the_check():
    from agent.response_shapes import response_shape_mismatches
    with tempfile.TemporaryDirectory() as tmp:
        root = _project(Path(tmp), {"repos": V124_BRANCHES_ROUTE}, {"pages/FilePage.tsx": V124_FILE_PAGE,
                                                                    "components/BranchSelector.tsx": V124_BRANCH_SELECTOR})
        app = root / "backend" / "src" / "app.js"
        app.write_text(app.read_text(encoding="utf-8").replace(
            "const app = express();\n", "const app = express();\napp.use((req, res, next) => { installEnvelope(res); next(); });\n"),
            encoding="utf-8")
        why: list[str] = []
        assert response_shape_mismatches(root, why) == [] and "a middleware hands the response to another function" in why, why

@case
def an_ordinary_middleware_followed_by_a_route_keeps_the_check():
    from agent.response_shapes import response_shape_mismatches, callback_body
    with tempfile.TemporaryDirectory() as tmp:
        root = _project(Path(tmp), {"repos": V124_BRANCHES_ROUTE}, {"pages/FilePage.tsx": V124_FILE_PAGE,
                                                                    "components/BranchSelector.tsx": V124_BRANCH_SELECTOR})
        app = root / "backend" / "src" / "app.js"
        ready = ("app.use((req, res, next) => {\n  databaseReady.then(() => next()).catch(() => res.status(503).json({ error: 'x' }));\n});\n"
                 "app.get('/api/health', (req, res) => { res.json({ ok: true }); });\n")
        app.write_text(app.read_text(encoding="utf-8").replace("const app = express();\n", "const app = express();\n" + ready),
                       encoding="utf-8")
        why: list[str] = []
        found = response_shape_mismatches(root, why)
        assert len(found) == 1 and "a middleware hands the response to another function" not in why, (found, why)
    code = "app.use((req, res, next) => { a(); }); app.get('/x', (req, res) => b(req, res));"
    assert callback_body(code, code.index("res, ") + 5) == "{ a(); }"

    with tempfile.TemporaryDirectory() as tmp:
        root = _project(Path(tmp), {"repos": V124_BRANCHES_ROUTE}, {"pages/FilePage.tsx": V124_FILE_PAGE,
                                                                    "components/BranchSelector.tsx": V124_BRANCH_SELECTOR})
        app = root / "backend" / "src" / "app.js"
        fake = ("const note = 'app.use((req, res, next), ';\n"
                "app.get('/api/x', (req, res) => { if (req.query.w) { wrap(res); } res.json({ ok: true }); });\n")
        app.write_text(app.read_text(encoding="utf-8").replace("const app = express();\n", "const app = express();\n" + fake),
                       encoding="utf-8")
        why = []
        found = response_shape_mismatches(root, why)
        assert len(found) == 1 and "a middleware hands the response to another function" not in why, (found, why)

@case
def v124_an_array_route_read_by_a_named_key_is_reported():
    found, why = _mismatches({"repos": V124_BRANCHES_ROUTE}, {"pages/FilePage.tsx": V124_FILE_PAGE,
                                                              "components/BranchSelector.tsx": V124_BRANCH_SELECTOR})
    assert len(found) == 1, (found, why)
    item = found[0]
    assert (item.method, item.route, item.shape.kind) == ("GET", "/api/repos/:owner/:repo/branches", "array"), item
    assert item.shape.where == "backend/src/routes/repos.js:23", item.shape.where
    assert [(r.file, r.line, r.name) for r in item.reads] == [("frontend/src/pages/FilePage.tsx", 20, "branches")], item.reads

@case
def v103_an_envelope_read_as_the_bare_user_is_reported_and_the_unwrap_is_not():
    found, why = _mismatches({"auth": V103_ME_ROUTE}, {"App.tsx": V103_APP, "components/Layout.tsx": V103_LAYOUT})
    assert len(found) == 1, (found, why)
    item = found[0]
    assert (item.route, item.shape.kind, sorted(item.shape.keys), item.shape.open) == ("/api/me", "object", ["user"], False)
    assert {(r.file, r.name) for r in item.reads} == {("frontend/src/components/Layout.tsx", "username")}, item.reads

@case
def the_finding_names_both_sides_and_carries_only_the_generic_fix():
    from agent.response_shapes import response_shape_findings
    logged: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = _project(Path(tmp), {"repos": V124_BRANCHES_ROUTE}, {"pages/FilePage.tsx": V124_FILE_PAGE})
        findings = response_shape_findings(root, log=logged.append)
    assert len(findings) == 1 and set(findings[0]) == {"where", "requirement", "problem", "fix"}, findings
    f = findings[0]
    assert f["where"] == "frontend/src/pages/FilePage.tsx:20"
    assert "backend/src/routes/repos.js:23" in f["problem"] and ".branches" in f["problem"] and "an array" in f["problem"]
    assert f["fix"].startswith("Return the shape the pages read, or read the shape the route returns; one shape per route")
    assert any("response shapes: 1 mismatch" in line for line in logged), logged

@case
def detaudit_reports_it_in_the_scripted_layer():
    from agent import detaudit
    with tempfile.TemporaryDirectory() as tmp:
        root = _project(Path(tmp), {"repos": V124_BRANCHES_ROUTE}, {"pages/FilePage.tsx": V124_FILE_PAGE})
        findings = detaudit.deterministic_findings(root, {"id": "REQ-X", "title": "t", "description": "d"}, [], log=lambda _m: None)
    assert any("/api/repos/:owner/:repo/branches returns an array" in f["problem"] for f in findings), findings

@case
def an_object_used_as_an_array_is_reported_also_when_open():
    routes = {"items": "router.get('/items', async (req, res) => {\n  const rows = await all('SELECT 1');\n"
                       "  res.json({ ...meta, items: rows });\n});\n"
                       "router.get('/tags', (req, res) => res.json({ tags: [], total: 0 }));\n"}
    pages = {"pages/List.tsx": "import { useData, peek } from '../api/store';\nexport function L() {\n"
                               "  const list = useData<Item[]>('/items') ?? [];\n  return list.map((i) => i.id);\n}\n"
                               "export function T() {\n  return peek<string[]>('/tags')?.length;\n}\n"}
    found, why = _mismatches(routes, pages)
    got = {(m.route, m.kind, tuple(r.name for r in m.reads)) for m in found}
    assert got == {("/api/items", "array", ("map",)), ("/api/tags", "array", ("length",))}, (got, why)

@case
def a_closed_object_read_by_a_key_it_lacks_and_axios_data_reads():
    routes = {"stats": "router.get('/stats', async (req, res) => {\n  const payload = { count: 1, names: [] };\n  res.json(payload);\n});\n"
                       "router.get('/rows', async (req, res) => {\n  res.json(await all('SELECT 1'));\n});\n"}
    pages = {"pages/S.tsx": "import apiClient from '../api';\nexport async function s() {\n"
                            "  const { data } = await apiClient.get('/stats');\n  const r = await apiClient.get('/rows');\n"
                            "  return [data.count, data.total, r.data.rows, r.data.length, r.status];\n}\n"}
    found, why = _mismatches(routes, pages)
    got = {(m.route, tuple(r.name for r in m.reads)) for m in found}
    assert got == {("/api/stats", ("total",)), ("/api/rows", ("rows",))}, (got, why)

@case
def a_page_that_tests_the_shape_is_silent():

    routes = {"orgs": "router.get('/organizations', async (req, res, next) => {\n  try {\n"
                      "    const orgs = await all('SELECT id, name, display_name FROM organizations ORDER BY name');\n"
                      "    res.json(orgs);\n  } catch (err) {\n    next(err);\n  }\n});\n"}
    pages = {"pages/OrganizationPage.tsx": "import { peek } from '../api/store';\nfunction OrganizationPage() {\n"
             "  const orgsData = peek<Org[] | { organizations: Org[] }>('/organizations');\n"
             "  const orgs = Array.isArray(orgsData) ? orgsData : orgsData?.organizations ?? [];\n  return orgs;\n}\n"}
    found, why = _mismatches(routes, pages)
    assert found == [], found

@case
def matching_reads_are_silent():
    routes = {"a": "router.get('/items', async (req, res) => {\n  const rows = await all('SELECT 1');\n  res.json(rows.map((r) => r.id));\n});\n"
                   "router.get('/me', (req, res) => {\n  res.json({ user: null, roles: [] });\n});\n"}
    pages = {"pages/P.tsx": "import { useData } from '../api/store';\nexport function P() {\n"
                            "  const items = useData<number[]>('/items') ?? [];\n  const me = useData<{ user: U }>('/me');\n"
                            "  return [items.length, items.filter(Boolean), items[0], me?.user, me?.roles.length, me?.toString()];\n}\n"}
    found, why = _mismatches(routes, pages)
    assert found == [], found

@case
def an_open_object_read_by_an_unlisted_key_is_silent():
    routes = {"r": "router.get('/repo', async (req, res) => {\n  const repository = await get('SELECT 1');\n"
                   "  res.json({ ...repository, repository });\n});\n"}
    pages = {"pages/P.tsx": "import { useData } from '../api/store';\nexport function P() {\n"
                            "  const repo = useData<Repo>('/repo');\n  return repo?.name;\n}\n"}
    assert _mismatches(routes, pages)[0] == []

@case
def uncertain_backend_answers_are_silent():
    routes = {"r": (

        "router.get('/a', (req, res) => {\n  res.status(code).json({ x: 1 });\n});\n"

        "router.get('/b', (req, res) => {\n  if (req.query.q) return sendList(res);\n  res.json({ x: 1 });\n});\n"

        "router.get('/c', (req, res, next) => {\n  if (!req.user) return next();\n  res.json({ x: 1 });\n});\n"

        "router.get('/d', async (req, res) => {\n  const row = await get('SELECT 1');\n  res.json(row);\n});\n"

        "router.get('/e', (req, res) => {\n  if (req.query.all) return res.json([]);\n  res.json({ x: 1 });\n});\n"

        "router.get('/f', listF);\n"

        "router.get('/g', (req, res) => {\n  db.all('SELECT 1', (err, res2) => res.json(res2));\n  helper((res) => res.json({}));\n});\n"

        "router.get('/h', async (req, res) => {\n  const rows = await all('SELECT 1');\n  res.json(rows?.map((r) => r));\n});\n"
    )}
    reads = "".join(f"  const v{c} = useData<X>('/{c}');\n  out.push(v{c}.y, v{c}.map(f));\n" for c in "abcdefgh")
    pages = {"pages/P.tsx": "import { useData } from '../api/store';\nexport function P() {\n  const out = [];\n" + reads + "  return out;\n}\n"}
    found, why = _mismatches(routes, pages)
    assert found == [], [(m.route, m.shape) for m in found]

@case
def error_answers_do_not_count():
    routes = {"r": "router.get('/x', (req, res) => {\n  if (!ok) return res.status(404).json({ error: 'no' });\n"
                   "  if (bad) { res.status(user ? 403 : 401).json({ error: 'denied' }); return; }\n  res.json([]);\n});\n"}
    pages = {"pages/P.tsx": "import { useData } from '../api/store';\nexport function P() {\n  const v = useData<X>('/x');\n  return v?.error;\n}\n"}
    found, _ = _mismatches(routes, pages)
    assert [(m.route, [r.name for r in m.reads]) for m in found] == [("/api/x", ["error"])], found

@case
def two_routes_that_may_answer_are_silent():
    routes = {"a": "router.get('/repos/:owner/:repo/branches', (req, res) => res.json([]));\n",
              "b": "router.get('/repos/:owner/:repo/:tab', (req, res) => res.json({ tab: 1 }));\n"}
    pages = {"pages/P.tsx": "import { useData } from '../api/store';\nexport function P({ o, r }: Props) {\n"
                            "  const b = useData<X>(`/repos/${o}/${r}/branches`);\n  return b?.branches;\n}\n"}
    assert _mismatches(routes, pages)[0] == []

@case
def a_shadowed_or_reassigned_binding_is_silent():
    routes = {"a": "router.get('/items', (req, res) => res.json([]));\n"}
    pages = {"pages/P.tsx": "import { useData } from '../api/store';\nexport function P() {\n"
                            "  const items = useData<X>('/items');\n  return groups.map((items) => items.label);\n}\n",
             "pages/Q.tsx": "import { useData } from '../api/store';\nexport function Q() {\n"
                            "  let items = useData<X>('/items');\n  items = normalise(items);\n  return items.label;\n}\n",
             "pages/R.tsx": "import { useData } from '../api/store';\nexport function R(p: string) {\n"
                            "  const v = useData<X>(p);\n  const w = useData<X>(cond ? '/items' : null);\n  return [v.label, w.label];\n}\n"}
    assert _mismatches(routes, pages)[0] == []

@case
def comments_strings_and_unmounted_backends_are_silent():
    routes = {"a": "router.get('/items', (req, res) => res.json([]));\n"}
    pages = {"pages/P.tsx": "import { useData } from '../api/store';\nexport function P() {\n"
                            "  const items = useData<X>('/items');\n  // items.label\n  /* items.total */\n"
                            "  return <p title=\"items.name\">{items.length}</p>;\n}\n"}
    assert _mismatches(routes, pages)[0] == []
    from agent.response_shapes import response_shape_mismatches
    with tempfile.TemporaryDirectory() as tmp:
        root = _project(Path(tmp), routes, {"pages/P.tsx": "import { useData } from '../api/store';\nconst v = useData<X>('/items');\nv.label;\n"})
        (root / "backend" / "src" / "app.js").write_text("const app = express();\nmodule.exports = app;\n", encoding="utf-8")
        why: list[str] = []
        assert response_shape_mismatches(root, why) == [] and why and why[0].startswith("no mount found"), why

@case
def a_regex_literal_with_quotes_does_not_blind_the_backend():

    routes = {"a": "router.get('/items', (req, res) => {\n  const fm = /filename=\"([^\"]*)\"/i.exec(h) || /filename=([^;\\r\\n]+)/i.exec(h);\n"
                   "  res.json([]);\n});\n"}
    pages = {"pages/P.tsx": "import { useData } from '../api/store';\nexport function P() {\n  const v = useData<X>('/items');\n  return v?.label;\n}\n"}
    found, why = _mismatches(routes, pages)
    assert [m.route for m in found] == ["/api/items"], (found, why)
    from agent.response_shapes import blank_strings
    assert blank_strings("x = a / b / c; y = </p>;") == "x = a / b / c; y = </p>;"

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
    print(f"{len(CASES) - failed}/{len(CASES)} response-shape cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
