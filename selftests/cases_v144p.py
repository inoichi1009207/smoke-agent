from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
from pathlib import Path
from types import SimpleNamespace

ROOT = Path.cwd() if (Path.cwd() / "main.py").is_file() else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES = []

def case(function):
    CASES.append(function)
    return function

def QUIET(*_a, **_k):
    return None

def fresh():
    from agent import fsutil
    fsutil.STOPPING.clear()

SHEET = "Pressing Enter or clicking another cell commits the change; pressing Escape cancels an uncommitted change."
AS_TYPED = " A committed cell value is stored exactly as typed."

def said(text=SHEET + AS_TYPED):
    from agent.commit_probe import key_statements
    return key_statements(text)

def result(**formula):
    base = {"mode": "type", "cell": "C5", "formula": "=512+9", "value": "521", "typed": True, "display": "521",
            "evaluated": True, "rawBefore": {"formula": True, "via": "selected"}, "overEditor": "521", "overShown": True,
            "displayAfter": "521", "rawAfter": {"formula": True, "via": "selected"}, "kept": True}
    base.update(formula)
    return {"thing": "cell", "route": "/workbooks/1", "role": "gridcell", "instances": 48, "modes": [], "escapes": [],
            "keys": {"enter": {"mode": "type", "cell": "D1", "below": "D2", "shown": True, "movedDown": True,
                               "stayed": False, "stored": True},
                     "formula": base, "ms": 9000}}

def findings(statement, res, log=QUIET):
    from agent.commit_probe import keys_findings
    return keys_findings(statement, res, log=log)

def formula_found(found):
    return [f for f in found if f["problem"].startswith("Typing a formula cell's displayed value")]

@case
def as_typed_is_recognised_positively_and_for_cells_only():
    for text in ("A committed cell value is stored exactly as typed.",
                 "Each cell saves its value as entered.",
                 "Entering a new value in a cell replaces its formula.",
                 "Committing an edit replaces the cell's previous content."):
        got = said(text)
        assert got and "as_typed" in got["said"], (text, got)
    for text in ("The cell value is not stored as typed.",
                 "A cell value is never saved exactly as typed.",
                 "A cell value isn't kept as typed.",
                 "The search box keeps the cell query as typed.",
                 "The value is stored exactly as typed.",
                 ""):
        got = said(text)
        assert not got or "as_typed" not in got["said"], (text, got)

    assert said(SHEET) == {"thing": "cell", "roles": ["gridcell", "cell"],
                           "said": {"enter_commits": "Pressing Enter or clicking another cell commits the change;"}}

@case
def the_formula_finding_needs_the_statement_and_every_observation():
    lines = []
    found = formula_found(findings(said(), result(), log=lines.append))
    assert len(found) == 1, found
    f = found[0]
    assert "stored as typed" in f["requirement"] and "“=512+9”" in f["problem"] and "“521”" in f["problem"], f
    assert "input showing the selected cell" in f["problem"] and "RAW content" in f["fix"], f
    assert set(f) == {"where", "requirement", "problem", "fix"} and f["where"] == "route /workbooks/1"

    replace = formula_found(findings(said(SHEET + " Selecting a cell and typing replaces its content."), result()))
    assert len(replace) == 1 and "typing replaces the content" in replace[0]["requirement"], replace

    lines.clear()
    assert formula_found(findings(said(SHEET), result(), log=lines.append)) == []
    assert any("(diagnostic, not reported)" in line and '"formula"' in line and '"kept": true' in line for line in lines), lines

    for missing in ({"kept": False}, {"kept": None}, {"rawBefore": {"formula": False, "via": "none"}}, {"rawBefore": None},
                    {"overShown": False}, {"evaluated": False}, {"evaluated": None}):
        assert formula_found(findings(said(), result(**missing))) == [], missing

    res = result()
    del res["keys"]["formula"]
    assert formula_found(findings(said(), res)) == []

@case
def the_js_formula_phase_is_gated_and_never_takes_visible_for_typed():
    from agent.commit_probe import PROBE_SCRIPT as s
    assert "const tq = fe && fe.stored === true && !keysLate()" in s, "the phase must need Enter storing in this grid"
    assert "r.overShown = norm(r.overEditor) === norm(value);" in s, "the editor must hold exactly the displayed value"
    assert "r.evaluated = display.includes(value) && !display.includes('=');" in s
    assert "r.kept = r.rawAfter.formula;" in s and "r.rawBefore = await raw(tq, formula);" in s

    assert s.index("if (job.keys) {") < s.index("const tq = fe") < s.index("out.keys.ms = Date.now() - k0;")
    assert s.index("out.keys.replace = r;") < s.index("const tq = fe")

@case
def the_pass_writes_through_source_io_with_the_same_bytes():
    from agent import description_pass as dp, source_io
    seen = []
    original = source_io.put_back
    try:
        source_io.put_back = lambda path, content: seen.append((Path(path).name, content))
        dp.write_text(Path("x") / "a.spec.js", "line 1\r\nline 2 ü\n")
        dp.remove_file(Path("x") / "b.spec.js")
    finally:
        source_io.put_back = original
    assert seen == [("a.spec.js", "line 1\r\nline 2 ü\n".encode("utf-8")), ("b.spec.js", None)], seen
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "backend" / "test-e2e" / "DESC-1.spec.js"
        dp.write_text(target, "a\r\nb ü\n")
        assert target.read_bytes() == "a\r\nb ü\n".encode("utf-8")
        assert sorted(p.name for p in target.parent.iterdir()) == ["DESC-1.spec.js"], "no temporary file is left"

@case
def a_pass_write_waits_for_the_watchdogs_lock_and_never_lands_after_the_stop():
    from agent import description_pass as dp, fsutil, source_io
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "spec.js"
        target.write_text("verified", encoding="utf-8")
        held, release = threading.Event(), threading.Event()

        def watchdog_put_back():
            with source_io.WRITE_LOCK:
                held.set()
                release.wait(5)

        dog = threading.Thread(target=watchdog_put_back, name="watchdog-test", daemon=True)
        dog.start()
        assert held.wait(5)
        writer = threading.Thread(target=lambda: dp.write_text(target, "half-verified"), name="main-like", daemon=True)
        writer.start()
        time.sleep(0.4)
        assert target.read_text(encoding="utf-8") == "verified", "the pass wrote while the watchdog held the lock"
        fsutil.STOPPING.set()
        release.set()
        dog.join(5)
        time.sleep(0.4)
        assert target.read_text(encoding="utf-8") == "verified", "the pass's write landed after the stop"
        assert writer.is_alive(), "the writer must park, not return"

    fresh()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "spec.js"
        with source_io.WRITE_LOCK:
            writer = threading.Thread(target=lambda: dp.write_text(target, "new"), daemon=True)
            writer.start()
            time.sleep(0.3)
            assert not target.exists()
        writer.join(5)
        assert target.read_text(encoding="utf-8") == "new"

@case
def the_pass_deletes_through_put_back_and_no_plain_write_is_left():
    from agent import description_pass as dp, source_io
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        folder = root / "backend" / "test-e2e"
        folder.mkdir(parents=True)
        (folder / "DESC-REQ-1.spec.js").write_text("x", encoding="utf-8")
        (folder / "DESC-REQ-2.spec.js").write_text("y", encoding="utf-8")
        p = dp.DescriptionPass.__new__(dp.DescriptionPass)
        p.dir, p.log = root, QUIET
        p.state = {"admitted": ["backend/test-e2e/DESC-REQ-1.spec.js"]}
        seen = []
        original = source_io.put_back

        def spy(path, content):
            seen.append((Path(path).relative_to(root).as_posix(), content))
            return original(path, content)

        source_io.put_back = spy
        try:
            assert p.remove_leftovers() == ["backend/test-e2e/DESC-REQ-2.spec.js"]
            p.keep_dropped("backend/test-e2e/DESC-REQ-1.spec.js", "dropped content")
        finally:
            source_io.put_back = original
        assert seen == [("backend/test-e2e/DESC-REQ-2.spec.js", None),
                        (".arc/description-pass/dropped/DESC-REQ-1.spec.js", b"dropped content"),
                        ("backend/test-e2e/DESC-REQ-1.spec.js", None)], seen
        assert not folder.joinpath("DESC-REQ-1.spec.js").exists() and not folder.joinpath("DESC-REQ-2.spec.js").exists()

    tree = ast.parse((ROOT / "agent" / "description_pass.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("fsutil"):
            assert "write_text" not in [a.name for a in node.names], "description_pass imports fsutil.write_text"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in ("write_text", "write_bytes", "unlink", "rmdir"), \
                f"plain {node.func.attr}() at line {node.lineno}"
    assert dp.write_text.__module__ == "agent.description_pass"

P2_REL = "backend/test-e2e/DESC-REQ-3.spec.js"
P2_HEAD = 'const { test, expect } = require("@playwright/test");\n\n'
P2_T1 = "REQ-3: the summary shows the total of the selected rows"
P2_T2 = "REQ-3: the summary is shown below the grid"
P2_DESCRIPTION = ("The summary shows the total of the selected rows as \"Selected total: N\". The summary is shown below "
                  "the grid.")
P2_REASON = ('TEST DEFECT: the test expects "Selected total: 6" but the page shows "Total: 5" after the selection; the '
             'description does not say the label.')

def p2_spec(first_literal: str, second_literal: str = "summary") -> str:
    return (P2_HEAD
            + f'// source: "The summary shows the total of the selected rows"\n'
            + f'test("{P2_T1}", async ({{ page }}) => {{\n  await expect(page.getByText("{first_literal}")).toBeVisible();\n}});\n\n'
            + f'test("{P2_T2}", async ({{ page }}) => {{\n  await expect(page.getByRole("region", {{ name: "{second_literal}" }})).toBeVisible();\n}});\n')

def p2_hand_back(answer: str, content: str | None = None):
    from agent import description_pass as dp
    content = content if content is not None else p2_spec("Selected total: 6")
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    (root / P2_REL).parent.mkdir(parents=True)
    (root / P2_REL).write_text(content, encoding="utf-8")
    p = dp.DescriptionPass.__new__(dp.DescriptionPass)
    lines: list[str] = []
    runs: list[str] = []
    p.dir, p.log, p.last_verdict, p.token_budget = root, lines.append, P2_REASON, 10 ** 9
    p.elapsed, p.spent_tokens = (lambda: 0.0), (lambda: 0)
    p.author = lambda view, prefix, problem, current: SimpleNamespace(content=answer)
    p.recheck = lambda view, prefix, spec: spec
    p.quote_gate = lambda text, description, tests: text

    def run_e2e(d, rels, log=None):
        runs.append((root / P2_REL).read_text(encoding="utf-8"))
        return SimpleNamespace(skipped=False, ok=True, output="")

    p.tools = SimpleNamespace(static_defects=lambda d, rels: "", run_e2e=run_e2e,
                              build_frontend=lambda d, log=None: SimpleNamespace(ok=True))
    tests: dict = {}
    entry: dict = {}
    got = p.hand_back_test_defect("REQ-3", {}, "DESC-REQ-3", P2_REL, content, P2_DESCRIPTION, tests, 1e9, entry)
    on_disk = (root / P2_REL).read_text(encoding="utf-8")
    tmp.cleanup()
    return got, tests, entry, on_disk, runs, lines

@case
def p2_new_literals_named_only_by_the_verdict_are_found():
    from agent.description_pass import toward_app_literals as f
    old = 'test("t", async () => { await expect(x.getByText("Selected total: 6")).toBeVisible(); });'
    assert f(old, old.replace("Selected total: 6", "Total: 5"), P2_REASON, P2_DESCRIPTION) == ["total: 5"]

    assert f(old, old.replace("Selected total: 6", "Selected total"), P2_REASON + ' "Selected total"', P2_DESCRIPTION) == []

    assert f(old, old, P2_REASON, P2_DESCRIPTION) == []
    assert f(old, old.replace("Selected total: 6", "Grand sum"), P2_REASON, P2_DESCRIPTION) == []
    assert f('x("ab")', 'x("5 ")', 'shows "5 "', "") == []

    assert f("", "a('Total:  5'); b(`grid`)", P2_REASON.upper(), P2_DESCRIPTION) == ["total: 5"]
    assert f("", 'a("say \\"Total: 5\\"")', 'shows say "Total: 5"', "") == ['say "total: 5"']

    assert f("", "x.getByRole('button'); y('page')", "the button on the page is disabled", "") == []
    assert f("", "x.getByText('Disabled')", 'the page shows "Disabled" now', "") == ["disabled"]

@case
def p2_a_test_rewritten_toward_the_verdict_is_cut_and_the_rest_goes_on():
    got, tests, entry, on_disk, runs, lines = p2_hand_back(p2_spec("Total: 5", "Summary"))
    assert got is not None, (entry, lines)
    new_content, _run, titles = got
    assert titles == [P2_T2] and P2_T1 not in new_content and "Total: 5" not in on_disk and P2_T2 in on_disk, on_disk
    assert tests[P2_T1] == {"title": P2_T1, "final": "dropped", "reason": "test-defect-toward-app"}, tests
    assert entry["test_defect_hand_back"]["toward_app"] == [P2_T1] and runs == [on_disk], entry
    assert any("rewritten toward the verdict's values" in line and "total: 5" in line for line in lines), lines

@case
def p2_a_rewrite_toward_the_description_or_an_unchanged_answer_is_taken_as_before():
    got, tests, entry, on_disk, runs, lines = p2_hand_back(p2_spec("Selected total: 5"))
    assert got is not None and got[2] == [P2_T1, P2_T2] and "Selected total: 5" in on_disk, (entry, lines)
    assert "toward_app" not in entry["test_defect_hand_back"] and not any(t.get("final") for t in tests.values()), tests
    same = p2_spec("Selected total: 6")
    got, tests, entry, on_disk, runs, lines = p2_hand_back(same)
    assert got is not None and entry["test_defect_hand_back"]["unchanged"] is True and on_disk == same

@case
def p2_every_test_toward_the_verdict_or_an_unsplittable_file_leaves_the_cut_as_before():
    original = p2_spec("Selected total: 6")
    only = P2_HEAD + f'test("{P2_T1}", async ({{ page }}) => {{\n  await expect(page.getByText("Total: 5")).toBeVisible();\n}});\n'
    got, tests, entry, on_disk, runs, lines = p2_hand_back(only)
    assert got is None and on_disk == original and runs == [] and tests == {}, (entry, tests)
    assert entry["test_defect_hand_back"]["result"] == "the rewrite adopts values only the verdict names", entry
    described = P2_HEAD + "test.describe('REQ-3', () => {\n" + original[len(P2_HEAD):] + "});\n"
    toward = described.replace("Selected total: 6", "Total: 5")
    got, tests, entry, on_disk, runs, lines = p2_hand_back(toward, content=described)
    assert got is None and on_disk == described and runs == [], entry
    assert entry["test_defect_hand_back"]["result"].startswith("the rewrite adopts values only the verdict names: ['total: 5']")

FIXTURE_SERVER = r"""
const http = require('http');
const MODE = process.env.FIXTURE_MODE === 'correct' ? 'correct' : 'broken';
const cells = { A1: 'Region', B1: 'Sales' };
const PAGE = `<!doctype html><html><head><meta charset="utf-8"><title>Grid</title><style>
body{font:14px sans-serif;margin:16px} [role=row]{display:flex} .cell{width:90px;height:28px;border:1px solid #999;
box-sizing:border-box;padding:2px;overflow:hidden} .cell[aria-selected=true]{outline:2px solid #36c} .cell input{width:100%;
height:100%;border:0;padding:0;font:inherit;box-sizing:border-box}</style></head><body><h1>Sheet</h1>
<input id="bar" aria-label="Formula bar" readonly style="width:360px"><div role="grid" aria-label="Sheet" id="g" tabindex="0"></div><script>
const MODE = '${MODE}';
const COLS = 'ABCD', ROWS = 7;
let cells = {}; let sel = null; let edit = null;
const ids = []; for (let r = 1; r <= ROWS; r++) for (const c of COLS) ids.push(c + r);
const show = (raw) => { const m = /^= *([0-9]+) *[+] *([0-9]+) *$/.exec(raw || ''); return m ? String(Number(m[1]) + Number(m[2])) : (raw || ''); };
const save = (id, value) => fetch('/api/cells/' + id, { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ value }) });
const step = (id, dc, dr) => { const c = COLS.indexOf(id[0]) + dc, r = Number(id.slice(1)) + dr;
  return c >= 0 && c < COLS.length && r >= 1 && r <= ROWS ? COLS[c] + r : id; };
const commit = () => { const e = edit; edit = null; if (!e) return;
  if (MODE === 'broken' && e.draft === show(cells[e.id])) return;   // A11: "unchanged" judged on the displayed value
  if (e.draft !== (cells[e.id] || '')) { cells[e.id] = e.draft; save(e.id, e.draft); } };
function render() {
  document.getElementById('bar').value = sel ? (cells[sel] || '') : '';
  const g = document.getElementById('g'); g.innerHTML = '';
  for (let r = 0; r < ROWS; r++) {
    const row = document.createElement('div'); row.setAttribute('role', 'row'); g.appendChild(row);
    for (const id of ids.slice(r * 4, r * 4 + 4)) {
      const cell = document.createElement('div'); cell.setAttribute('role', 'gridcell'); cell.setAttribute('aria-label', id);
      cell.setAttribute('aria-selected', String(sel === id)); cell.className = 'cell'; cell.dataset.id = id; row.appendChild(cell);
      if (edit && edit.id === id) {
        const input = document.createElement('input'); input.value = edit.draft; input.setAttribute('aria-label', 'Edit ' + id);
        input.addEventListener('input', () => { edit.draft = input.value; });
        cell.appendChild(input);
        setTimeout(() => { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }, 0);
      } else cell.textContent = show(cells[id]);
    }
  }
  if (!edit) document.getElementById('g').focus();
}
document.addEventListener('mousedown', (ev) => {
  const cell = ev.target.closest && ev.target.closest('[role=gridcell]');
  if (edit && !(cell && cell.dataset.id === edit.id)) commit();
  if (cell) { sel = cell.dataset.id; ev.preventDefault(); render(); }
}, true);
document.addEventListener('dblclick', (ev) => {
  const cell = ev.target.closest && ev.target.closest('[role=gridcell]');
  if (cell && !edit) { sel = cell.dataset.id; edit = { id: sel, draft: cells[sel] || '' }; render(); }
});
document.addEventListener('keydown', (ev) => {
  if (!sel) return;
  if (ev.key === 'Escape') { if (edit) { edit = null; render(); } return; }
  if (ev.key === 'Enter') { ev.preventDefault(); if (edit) { commit(); sel = step(sel, 0, 1); render(); } return; }
  if (ev.key === 'Tab') { ev.preventDefault(); if (edit) commit(); sel = step(sel, 1, 0); render(); return; }
  if (ev.key === 'ArrowDown') { ev.preventDefault(); if (edit) commit(); sel = step(sel, 0, 1); render(); return; }
  if (edit || ev.key.length !== 1 || ev.ctrlKey || ev.metaKey || ev.altKey) return;
  ev.preventDefault();
  edit = { id: sel, draft: ev.key }; render();
});
fetch('/api/cells').then((r) => r.json()).then((data) => { cells = data; render(); });
</script></body></html>`;
http.createServer((req, res) => {
  if (req.method === 'GET' && req.url === '/api/cells') { res.setHeader('content-type', 'application/json'); return res.end(JSON.stringify(cells)); }
  const m = req.url.match(/^\/api\/cells\/([A-Z]\d+)$/);
  if (req.method === 'PUT' && m) {
    let body = ''; req.on('data', (c) => { body += c; });
    return req.on('end', () => { cells[m[1]] = JSON.parse(body || '{}').value || ''; res.setHeader('content-type', 'application/json'); res.end('{}'); });
  }
  if (req.method === 'GET' && req.url.split('?')[0] === '/') { res.setHeader('content-type', 'text/html'); return res.end(PAGE); }
  res.statusCode = 404; res.end('not found');
}).listen(Number(process.env.PORT), '127.0.0.1');
"""

def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

def browser_fixture(mode: str, backend: Path, statement):
    from agent.commit_probe import keys_job, run_probe
    with tempfile.TemporaryDirectory() as tmp:
        server_js = Path(tmp) / "fixture.cjs"
        server_js.write_text(FIXTURE_SERVER, encoding="utf-8")
        port = _free_port()
        server = subprocess.Popen(["node", str(server_js)], env={**os.environ, "PORT": str(port), "FIXTURE_MODE": mode},
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)
                    break
                except Exception:
                    time.sleep(0.2)
            res = run_probe(backend, f"http://127.0.0.1:{port}", keys_job(statement, None), log=print)
            print(f"  {mode}: {json.dumps((res or {}).get('keys', res))[:900]}")
            assert isinstance(res, dict) and not res.get("error") and isinstance(res.get("keys"), dict), res
            return res, findings(statement, res)
        finally:
            server.kill()
            server.wait(timeout=10)

def browser_main() -> int:
    backend = Path(os.environ.get("ARC_PLAYWRIGHT_BACKEND") or ROOT / "template" / "backend")
    if not (backend / "node_modules" / "@playwright" / "test").is_dir():
        print(f"skipped  browser fixtures: no node_modules/@playwright/test under {backend} (set ARC_PLAYWRIGHT_BACKEND)")
        return 0
    failed = 0
    for mode, statement, want in (("broken", said(), 1), ("broken", said(SHEET), 0), ("correct", said(), 0)):
        try:
            res, found = browser_fixture(mode, backend, statement)
            formula = res["keys"].get("formula") or {}
            assert formula.get("evaluated") is True and (formula.get("rawBefore") or {}).get("formula") is True, formula
            assert formula.get("overShown") is True and formula.get("kept") is (mode == "broken"), formula
            got = len(formula_found(found))
            assert got == want, (mode, got, want)
            print(f"ok    browser fixture {mode} ({sorted(statement['said'])}): {got} formula finding(s)")
        except Exception:
            failed += 1
            print(f"FAIL  browser fixture {mode}")
            traceback.print_exc(limit=4)
    return failed

def main() -> int:
    if not __debug__:
        print("FAIL  self-tests need assertions: run without -O and without PYTHONOPTIMIZE")
        return 1
    started = time.time()
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=6)
        finally:
            fresh()
    print(f"{len(CASES) - failed}/{len(CASES)} v144p cases passed in {time.time() - started:.1f}s")
    if "--browser" in sys.argv:
        failed += browser_main()
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
