from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES = []
QUIET = lambda message: None

def case(function):
    CASES.append(function)
    return function

def page(aria: str, route: str = "/") -> dict:
    return {"route": route, "url": "http://127.0.0.1:3300" + route, "aria": aria, "links": []}

NOTE = ("Announcements are shown on the home page. Each note is an article named by its title on the notes page. "
        "Activating the note article opens a dialog named `Note editor`.")
HOME = page('- main:\n  - article "Release 2.0":\n    - heading "Release 2.0" [level=3]:\n      - button "Release 2.0"\n')
NOTES = page('- main:\n  - article "Sprint goals":\n    - heading "Sprint goals" [level=3]:\n      - button "Sprint goals"\n',
             route="/notes")

@case
def activation_probes_the_page_named_after_the_object_first():
    from agent.activation_probe import activation_statements, probe_routes
    said = activation_statements(NOTE)[0]
    assert probe_routes(said, [HOME, NOTES]) == ["/notes"], probe_routes(said, [HOME, NOTES])
    assert probe_routes(said, [NOTES, HOME]) == ["/notes"]

    other = page(NOTES["aria"], route="/archive")
    assert probe_routes(said, [other, HOME]) == ["/"]

    books = page(NOTES["aria"], route="/notebooks")
    assert probe_routes(said, [books, HOME]) == ["/"]

@case
def activation_reports_only_when_the_title_opens_the_stated_dialog():
    from agent.activation_probe import activation_findings, activation_statements
    said = activation_statements(NOTE)
    base = {"route": "/", "role": "article", "skipped": [], "item": "Release 2.0", "container": None}

    lines = []
    assert activation_findings(said, [dict(base, title={"dialog": "Release history"})], log=lines.append) == []
    assert any("no stated dialog" in line for line in lines), lines

    assert activation_findings(said, [dict(base, title={"dialog": ""})], log=QUIET) == []

    found = activation_findings(said, [dict(base, item="Sprint goals", title={"dialog": "note  EDITOR"})], log=QUIET)
    assert len(found) == 1 and "“Sprint goals”" in found[0]["problem"] and "`Note editor`" in found[0]["requirement"], found

    both = activation_statements(NOTE + " Activating the announcement article opens a dialog named `Release history`.")
    assert len(both) == 2, both
    found = activation_findings(both, [dict(base, title={"dialog": "Release history"})], log=QUIET)
    assert len(found) == 1 and "announcement article" in found[0]["problem"] and "`Release history`" in found[0]["requirement"], found

@case
def activation_probe_names_a_dialog_by_aria_labelledby():

    from agent.activation_probe import PROBE_SCRIPT
    dialogs = PROBE_SCRIPT[PROBE_SCRIPT.index("const dialogs"): PROBE_SCRIPT.index("async function opened")]
    assert "aria-labelledby" in dialogs and "aria-label'" in dialogs, dialogs

@case
def escape_of_another_object_is_not_bound_to_the_cell_edit():
    from agent.commit_probe import commit_statements
    said = commit_statements("Cells save immediately. Clicking another cell commits the change. "
                             "In the search dialog, pressing Escape cancels the search.")
    assert [(s["thing"], s["escape"], s["escape_sentence"]) for s in said] == [("cell", False, "")], said
    for text in ("Clicking another cell commits the change. Pressing Escape cancels the open menu.",
                 "Clicking another cell commits the change. The filter panel closes; pressing Escape cancels it.",
                 "Clicking another cell commits the change. Later on the page: in the cell picker Escape cancels."):
        assert commit_statements(text)[0]["escape"] is False, text

@case
def escape_of_the_same_edit_is_still_bound():
    from agent.commit_probe import commit_statements

    v141 = commit_statements("Pressing Enter or clicking another cell commits the change; pressing Escape cancels an "
                             "uncommitted change.")[0]
    assert v141["escape"] is True and v141["escape_sentence"].startswith("pressing Escape cancels"), v141

    assert commit_statements("Clicking another cell commits the change. Pressing Escape cancels the edit.")[0]["escape"]

    far = commit_statements("Clicking another cell commits the change. Totals update at once. Rows can be sorted. "
                            "While a cell is being edited, pressing Escape cancels the typed value.")[0]
    assert far["escape"] is True and "cell is being edited" in far["escape_sentence"], far

    assert commit_statements("Clicking another cell commits the change. Totals update at once. Rows can be sorted. "
                             "Pressing Escape cancels.")[0]["escape"] is False

    two = commit_statements("In the search dialog, Escape cancels the search. Clicking another cell commits the change; "
                            "Escape cancels an uncommitted change.")[0]
    assert two["escape"] is True and two["escape_sentence"].startswith("Escape cancels an uncommitted"), two

def _closing_view(unattempted: list[str]):
    from agent.description_pass import DescriptionPass
    work = [{"id": "B1", "members": ["REQ-1"], "description": "Insert a column into the sheet."},
            {"id": "B2", "members": ["REQ-2"], "description": "The `Add filter` button creates a filter."},
            {"id": "B3", "members": ["REQ-3"], "description": "The `Export` button downloads the sheet."}]
    batch = dict(work[0], later_names=["Add filter", "Export"], earlier_names=[])
    leaf = {"id": "REQ-1", "name": "Insert column", "description": "Inserting a column keeps the filter working."}
    owner = SimpleNamespace(whole="", work=work, f=SimpleNamespace(unattempted_members=unattempted),
                            tools=SimpleNamespace(note_channel="pass_note"))
    return DescriptionPass.leaf_view(owner, leaf, batch)

SPEC9 = 'await click(page, "Add filter");\nawait click(page, "Export");\n'

@case
def the_closing_pass_refuses_only_controls_of_batches_never_started():
    from agent.outsider import later_controls
    view = _closing_view([])
    assert later_controls(SPEC9, view) == [], view.get("unbuilt_names")
    assert view["later_names"] == ["Add filter", "Export"]

    view = _closing_view(["REQ-3"])
    assert later_controls(SPEC9, view) == ["Export"], later_controls(SPEC9, view)

    copy = {k: v for k, v in view.items() if k != "pass_note"}
    assert later_controls(SPEC9, copy) == ["Export"]

@case
def a_batch_still_refuses_controls_of_later_batches():
    from agent.outsider import later_controls
    node = {"id": "B1", "members": ["REQ-1"], "description": "Insert a column.", "later_names": ["Add filter", "Export"]}
    assert later_controls(SPEC9, node) == ["Add filter", "Export"]

NODE_STUB = r"""
%s
%s
const loc = (n, enabledAt) => ({
  filter: () => loc(n, enabledAt),
  count: async () => n,
  nth: (i) => ({ isVisible: async () => true, isEnabled: async () => i === enabledAt, isEditable: async () => i === enabledAt,
                 evaluate: async () => 'textbox', id: i }),
});
const isCheckable = async () => false;
const settle = async () => undefined;
(async () => {
  const hit = await firstVisible([loc(9, 8)], 0, true);
  const enabled = await presentNow([loc(9, 8)], 'enabled');
  const editable = await presentNow([loc(12, 11)], 'editable');
  const none = await firstVisible([loc(9, -1)], 0, true);
  console.log('R45C' + JSON.stringify({ hit: hit && hit.id, enabled, editable, none }));
})();
"""

def js_function(source: str, name: str) -> str:
    start = source.index(f"async function {name}(")
    return source[start: source.index("\n}\n", start) + 2]

def run_helper_stub(helper_js: str) -> dict | None:
    node = shutil.which("node")
    if not node:
        return None
    script = NODE_STUB % (js_function(helper_js, "firstVisible"), js_function(helper_js, "presentNow"))
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "stub.cjs").write_text(script, encoding="utf-8")
        done = subprocess.run([node, "stub.cjs"], cwd=tmp, capture_output=True, text=True, timeout=60, encoding="utf-8")
    line = next((x for x in (done.stdout or "").splitlines() if x.startswith("R45C")), "")
    assert line, (done.stdout or "") + (done.stderr or "")
    return json.loads(line[4:])

@case
def helper_lookups_look_past_eight_disabled_copies():
    from agent.outsider import HELPER_JS
    got = run_helper_stub(HELPER_JS)
    if got is None:
        print("      (skipped: no node)")
    else:
        assert got == {"hit": 8, "enabled": True, "editable": True, "none": None}, got

    fill = js_function(HELPER_JS, "fill")
    assert not re.search(r"Math\.min\(\s*count\s*,", fill), fill

DESC11 = "Inserting a column keeps the filter working after a reload."

def _gate(body: str, header: str = "") -> tuple[str, dict]:
    from agent.description_pass import DescriptionPass
    content = ('const { test } = require("@playwright/test");\n' + header + "\n"
               'test("REQ-1: inserting a column keeps the filter working", async ({ page }) => {\n'
               + body + '  await page.goto("/");\n});\n')
    owner = SimpleNamespace(log=QUIET)
    tests: dict = {}
    return DescriptionPass.quote_gate(owner, content, DESC11, tests), tests

@case
def a_skip_whose_condition_is_false_keeps_the_test():
    for body in ('  test.skip(false, "condition false");\n', "  test.fixme(false);\n", "  test.fail( false , 'never' );\n"):
        kept, tests = _gate(body)
        assert kept and "inserting a column" in kept, (body, tests)
    kept, _tests = _gate("", header='test.skip(false, "file scope");\n')
    assert kept, "a file-scope skip(false) skips nothing"

@case
def a_skip_that_can_apply_still_drops_the_test():
    for body in ('  test.skip(true, "always");\n', '  test.skip(process.env.CI === "1", "maybe");\n', "  test.skip();\n",
                 '  test.skip(false, "no");\n  test.fixme();\n', "  test.fixme(!false);\n"):
        kept, tests = _gate(body)
        assert kept == "" and list(tests.values())[0]["reason"] == "not-run-modifier", (body, tests)
    kept, _tests = _gate("", header="test.only(false);\n")
    assert kept == ""

BROWSER10 = r"""
const { chromium } = require('@playwright/test');
const H = require('./e2e-support/outsider');
const first = (e) => String((e && e.message) || e).split('\n')[0].slice(0, 160);
const doc = (body) => `<!doctype html><html><head><meta charset="utf-8"></head><body>${body}<p id=out></p>
<script>document.addEventListener('click', (e) => { if (e.target.id) document.getElementById('out').textContent = 'clicked ' + e.target.id; });</script></body></html>`;
const buttons = Array.from({ length: 9 }, (_, i) => `<button id=b${i} ${i < 8 ? 'disabled' : ''}>Save</button>`).join('');
const fields = Array.from({ length: 7 }, (_, i) => `<input id=f${i} placeholder="Note" ${i < 6 ? 'readonly' : ''}>`).join('');
(async () => {
  const browser = await chromium.launch();
  const out = {};
  for (const [name, body, run] of [
    ['click', buttons, async (page) => { await H.click(page, 'Save'); return page.locator('#out').innerText(); }],
    ['fill', fields, async (page) => { await H.fill(page, 'Note', 'hello'); return page.locator('#f6').inputValue(); }],
  ]) {
    const context = await browser.newContext();
    await context.route('http://probe.test/**', (r) => r.fulfill({ contentType: 'text/html', body: doc(body) }));
    const page = await context.newPage();
    await page.goto('http://probe.test/');
    try { out[name] = 'OK ' + (await run(page)); } catch (e) { out[name] = 'ERR ' + first(e); }
    await context.close();
  }
  await browser.close();
  console.log('R45C' + JSON.stringify(out));
})();
"""
SITE3 = """<!doctype html><html><body><main>
<article aria-label="Sprint goals" style="padding:40px;border:1px solid #000"><h3><button id="t1">Sprint goals</button></h3>
<p>Some text of the note body here.</p></article>
<div id="d" role="dialog" aria-labelledby="dt" hidden><p id="dt">Note editor</p></div>
</main><script>document.getElementById('t1').onclick = () => { document.getElementById('d').hidden = false; };</script></body></html>
"""

def browser_helper(backend: Path, helper_js: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "e2e-support").mkdir()
        (Path(tmp) / "e2e-support" / "outsider.js").write_text(helper_js, encoding="utf-8")
        (Path(tmp) / "b.cjs").write_text(BROWSER10, encoding="utf-8")
        done = subprocess.run(["node", "b.cjs"], cwd=tmp, capture_output=True, text=True, timeout=180, encoding="utf-8",
                              env={**os.environ, "NODE_PATH": str(backend / "node_modules")})
    line = next((x for x in (done.stdout or "").splitlines() if x.startswith("R45C")), "")
    return json.loads(line[4:]) if line else {"error": ((done.stdout or "") + (done.stderr or ""))[-400:]}

def browser_activation(backend: Path) -> list | None:
    from agent.activation_probe import run_probe
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "index.html").write_text(SITE3, encoding="utf-8")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"], cwd=tmp,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(1.5)
            return run_probe(backend, f"http://127.0.0.1:{port}", [{"route": "/", "role": "article"}], log=QUIET)
        finally:
            server.kill()
            server.wait(timeout=10)

def browser_main() -> int:
    backend = Path(os.environ.get("ARC_PLAYWRIGHT_BACKEND") or ROOT / "template" / "backend")
    if not (backend / "node_modules" / "@playwright" / "test").is_dir():
        print(f"skipped  browser checks: no node_modules/@playwright/test under {backend.name} (set ARC_PLAYWRIGHT_BACKEND)")
        return 0
    from agent.activation_probe import activation_findings, activation_statements
    from agent.outsider import HELPER_JS
    failed = 0
    got = browser_helper(backend, HELPER_JS)
    if got == {"click": "OK clicked b8", "fill": "OK hello"}:
        print("ok    browser #10: click and fill past eight disabled / six read-only copies")
    else:
        failed += 1
        print(f"FAIL  browser #10: {got}")
    results = browser_activation(backend)
    found = activation_findings(activation_statements(NOTE), results, log=QUIET)
    if results and results[0].get("title") == {"dialog": "Note editor"} and len(found) == 1:
        print("ok    browser #3: a dialog named by aria-labelledby is the stated one")
    else:
        failed += 1
        print(f"FAIL  browser #3: {results} -> {len(found)} finding(s)")
    return failed

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
    print(f"{len(CASES) - failed}/{len(CASES)} r45c cases passed")
    if "--browser" in sys.argv:
        failed += browser_main()
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
