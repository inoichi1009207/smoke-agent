from __future__ import annotations

import glob
import re
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import testrun
from agent.testrun import (PLAYWRIGHT_ROLES, frontend_routes, helper_role_words, route_matches,
                           static_test_defects, unknown_roles, unrouted_page_note)

CASES: list = []
SKIPPED: list[str] = []
REL = "backend/test-e2e/REQ-X.spec.js"
HEAD = ('const { test } = require("@playwright/test");\n'
        'const { expect, click, fill, choose, see, notSee, locate, item, region } = require("../e2e-support/outsider");\n\n')

def case(function):
    CASES.append(function)
    return function

def spec(body: str) -> str:
    return HEAD + 'test("REQ-X: t", async ({ page, request }) => {\n' + body + "\n});\n"

@case
def r1_password_input_target_is_an_e1_finding():
    text = spec('  await page.goto("/register");\n'
                '  await fill(page, { role: "password-input", name: "Password" }, "hunter2hunter2");\n'
                '  await click(page, { role: "button", name: "Create account" });')
    found = unknown_roles(REL, text)
    assert len(found) == 1 and ":6:" in found[0] and '"password-input" is no ARIA role' in found[0], found
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / REL
        target.parent.mkdir(parents=True)
        target.write_text(text, encoding="utf-8")
        report = static_test_defects(Path(tmp), [REL])
    assert "password-input" in report and "SPEC DEFECT" in report, report

@case
def r2_words_the_helper_accepts_are_left_alone():
    roles, fields, aliases = helper_role_words()
    body = "\n".join(f'  await see(page, {{ role: "{w}", name: "N" }});' for w in sorted(roles | fields | aliases))
    body += '\n  await click(page, { role: "Button", name: "Go" });\n  await fill(page, { role: "Password", name: "Password" }, "x");'
    assert unknown_roles(REL, spec(body)) == []

@case
def r3_role_outside_a_helper_target_is_left_alone():
    text = spec('  await request.post("/api/orgs/acme/members", { data: { username: "bob", role: "admin" } });\n'
                '  const member = { name: "bob", role: "owner" };\n'
                '  await request.put("/api/x", { data: member });\n'
                '  await click(page, [{ role: "bogus-in-array" }]);\n'
                '  // await click(page, { role: "commented-out" });\n'
                '  await see(page, "role: nothing");')
    assert unknown_roles(REL, text) == [], unknown_roles(REL, text)

@case
def r4_target_held_in_a_const_is_reported_once():
    text = spec('  const secret = { name: "Password", role: "secret-field" };\n'
                '  await fill(page, secret, "x");\n'
                '  await see(page, secret);')
    found = unknown_roles(REL, text)
    assert len(found) == 1 and ":5:" in found[0] and "secret-field" in found[0], found

@case
def r5_get_by_role_with_no_role():
    text = ('const { test } = require("@playwright/test");\n'
            'test("t", async ({ page }) => {\n'
            '  await page.getByRole("password").fill("x");\n'
            "  await page.getByRole('Button', { name: 'Go' }).click();\n"
            '  await page.getByRole("mark").first().isVisible();\n'
            '  await page.getByRole("paragraph").count();\n'
            '});\n')
    found = unknown_roles(REL, text)
    assert len(found) == 1 and ":3:" in found[0] and 'getByRole("password")' in found[0], found

@case
def r6_role_words_are_read_from_the_helper():
    words = helper_role_words()
    assert words is not None
    roles, fields, aliases = words
    assert {"button", "textbox", "combobox", "generic", "paragraph"} <= roles and "password" in fields and "select" in aliases
    assert roles <= PLAYWRIGHT_ROLES and testrun.ARIA_ROLES <= roles | {"mark"}, sorted(roles ^ PLAYWRIGHT_ROLES)

@case
def r7_only_e2e_specs_and_only_helper_imports():
    text = spec('  await fill(page, { role: "password-input", name: "P" }, "x");')
    assert unknown_roles("backend/tests/REQ-X.test.js", text) == []
    no_helper = text.replace('require("../e2e-support/outsider")', 'require("./mine")')
    assert unknown_roles(REL, no_helper) == []

def delivered_specs() -> list[Path]:
    return [Path(p) for p in glob.glob(str(ROOT.parent / "local-eval" / "runs" / "*" / "template" / "backend" / "test-e2e" / "*.spec.js"))]

@case
def r8_census_no_finding_on_delivered_specs():
    files = delivered_specs()
    if not files:
        SKIPPED.append("r8: no delivered specs here")
        return
    found = [f for p in files for f in unknown_roles("backend/test-e2e/" + p.name, p.read_text(encoding="utf-8", errors="replace"))]
    assert found == [], found[:5]

APP = """import { Route, Routes } from 'react-router-dom';
import Layout from './Layout';
import HomePage from './pages/HomePage';
import RepoPage from './pages/RepoPage';
import PullsPage from './pages/PullsPage';
import SettingsPage from './pages/SettingsPage';
export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<HomePage />} />
        <Route path=":owner/:repo" element={<RepoPage />} />
        <Route path=":owner/:repo/pulls" element={<PullsPage />} />
        <Route path="/settings/profile" element={<SettingsPage />} />
      </Route>
    </Routes>
  );
}
"""

def failure(url: str, clickable: str = "[]", headings: str = "[]") -> str:
    return (f"  1) test-e2e/REQ-X.spec.js:9:1 > REQ-X: t\n    Error: The requirement says Merged is shown, but nothing visible matches.\n"
            f"  url: {url}\n  on the page now -> clickable: {clickable}\n  fields: []\n  headings: {headings}\n  messages: []\n")

def project(app: str = APP, extra: dict[str, str] | None = None) -> tempfile.TemporaryDirectory:
    tmp = tempfile.TemporaryDirectory()
    src = Path(tmp.name) / "frontend" / "src"
    (src / "pages").mkdir(parents=True)
    (src / "App.tsx").write_text(app, encoding="utf-8")
    for rel, text in (extra or {}).items():
        (src / rel).write_text(text, encoding="utf-8")
    return tmp

@case
def u1_address_no_route_matches_is_stated():
    with project() as tmp:
        note = unrouted_page_note(Path(tmp), "E2E FAILED\n" + failure("http://127.0.0.1:3100/acme/docs/pull/1"))
    assert note.startswith(testrun.ROUTE_NOTE_HEAD), note
    assert "- /acme/docs/pull/1: NO route of the frontend router matches" in note and "/:owner/:repo/pulls" in note, note
    assert "frontend/src/App.tsx" in note and "showed no link, button, field, heading or message" in note, note

@case
def u2_routed_pages_blank_or_not():
    with project() as tmp:
        blank = unrouted_page_note(Path(tmp), failure("http://127.0.0.1:3100/acme/docs"))
        shown = unrouted_page_note(Path(tmp), failure("http://127.0.0.1:3100/acme/docs", clickable='["Pulls"]'))
        home = unrouted_page_note(Path(tmp), failure("http://127.0.0.1:3100/", headings='["Welcome"]'))
    assert "- /acme/docs (route /:owner/:repo): the page showed no link" in blank and "NO route" not in blank, blank
    assert shown == "" and home == "", (shown, home)

@case
def u3_matching_follows_react_router():
    with project() as tmp:
        routes = frontend_routes(Path(tmp))
        assert routes is not None and set(routes[0]) == {"/", "/:owner/:repo", "/:owner/:repo/pulls", "/settings/profile"}, routes
        assert unrouted_page_note(Path(tmp), failure("http://127.0.0.1:3100/Settings/Profile/", clickable='["Save"]')) == ""
        assert unrouted_page_note(Path(tmp), failure("http://127.0.0.1:3100/acme/docs/PULLS", clickable='["x"]')) == ""
        assert unrouted_page_note(Path(tmp), failure("http://127.0.0.1:3100/Acme%20Demo/docs/pulls", clickable='["x"]')) == ""
        assert unrouted_page_note(Path(tmp), failure("http://127.0.0.1:3100/api/repos/1")) == ""
        assert unrouted_page_note(Path(tmp), "Error: expect(locator).toBeVisible() failed\n  Locator: getByText('x')") == ""
        assert unrouted_page_note(Path(tmp), failure("about:blank")) == ""
    assert route_matches("/files/*", "/files") and route_matches("/files/*", "/files/a/b") and not route_matches("/files/*", "/other")
    assert route_matches("/:a/:b", "/x/y") and not route_matches("/:a/:b", "/x/y/z") and not route_matches("/:a", "/")

@case
def u4_unsure_routers_are_silent():
    unrouted = failure("http://127.0.0.1:3100/acme/docs/pull/1")
    catch_all = APP.replace('<Route path="/settings/profile"', '<Route path="*" element={<HomePage />} />\n        <Route path="/settings/profile"')
    variable = APP.replace('path="/settings/profile"', "path={SETTINGS}")

    lossy = APP.replace('<Route path="/settings/profile" element={<SettingsPage />} />',
                        '{/* <Route path="/old" element={<HomePage />} /> */}\n'
                        '        <Route path="/settings/profile" element={<SettingsPage />} />')
    v112_shape = APP.replace('<Route path="/settings/profile" element={<SettingsPage />} />',
                             '<Route path=":owner/:repo/tree/*" element={<RepoPage />} />\n'
                             '        {/* profile last */}\n'
                             '        <Route path="/settings/profile" element={<SettingsPage />} />')
    with project(catch_all) as tmp:
        note = unrouted_page_note(Path(tmp), unrouted)
    assert "NO route" not in note and "(route /*)" in note, note
    for app, extra in ((variable, None), (lossy, None),
                       (APP, {"routes.tsx": "export const r = useRoutes([{ path: '/x' }]);"}),
                       (APP.replace("<Routes>", "<BrowserRouter basename=\"/app\"><Routes>"), None)):
        with project(app, extra) as tmp:
            assert unrouted_page_note(Path(tmp), unrouted) == "", app[-400:]
    with project(lossy) as tmp:
        assert frontend_routes(Path(tmp)) is None, frontend_routes(Path(tmp))
    for app in (v112_shape, v112_shape.replace('path=":owner/:repo/tree/*"', 'path="/:owner/:repo/tree/*"')):
        with project(app) as tmp:
            routes = frontend_routes(Path(tmp))
            assert routes is not None and any("/settings/profile" in str(r) for r in routes), routes
            assert "NO route" in unrouted_page_note(Path(tmp), unrouted), app[-300:]

@case
def u7_note_leaves_the_failure_signature_alone():
    from agent.codegen import SIGNATURE_MARKS

    with project() as tmp:
        note = unrouted_page_note(Path(tmp), failure("http://127.0.0.1:3100/acme/docs/pull/1")
                                  + failure("http://127.0.0.1:3100/acme/docs"))
    assert note.count("\n- ") == 2, note
    assert not [m for m in SIGNATURE_MARKS if m in note], [m for m in SIGNATURE_MARKS if m in note]
    assert not [line for line in note.splitlines() if re.match(r"\s*\d+\) ", line)], note

@case
def u5_no_router_no_note():
    with tempfile.TemporaryDirectory() as tmp:
        assert unrouted_page_note(Path(tmp), failure("http://127.0.0.1:3100/x")) == ""
    with project("export default function App() { return <main>hi</main>; }\n") as tmp:
        assert unrouted_page_note(Path(tmp), failure("http://127.0.0.1:3100/x")) == ""

@case
def u6_census_every_goto_of_delivered_specs_is_routed():
    goto = re.compile(r"""\bgoto\(\s*["'](/[^"'`$?#]*)""")
    projects = [Path(p) for p in glob.glob(str(ROOT.parent / "local-eval" / "runs" / "*" / "template")) if (Path(p) / "frontend" / "src").is_dir()]
    if not projects:
        SKIPPED.append("u6: no delivered projects here")
        return
    unmatched = []
    for proj in projects:
        routes = frontend_routes(proj)
        if routes is None:
            continue
        for spec_file in (proj / "backend" / "test-e2e").glob("*.spec.js"):
            for target in set(goto.findall(spec_file.read_text(encoding="utf-8", errors="replace"))):
                if not target.startswith("/api") and not any(route_matches(r, target) for r in routes[0]):
                    unmatched.append((proj.parent.name, spec_file.name, target))
    assert unmatched == [], unmatched[:5]

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
            traceback.print_exc()
    print(f"{len(CASES) - failed}/{len(CASES)} self-tests passed" + (f"; {len(SKIPPED)} skipped" if SKIPPED else ""))
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
