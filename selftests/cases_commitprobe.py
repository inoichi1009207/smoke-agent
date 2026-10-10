from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES = []
QUIET = lambda message: None

def case(function):
    CASES.append(function)
    return function

SHEET = ("Selecting a cell and typing replaces its content, and double-clicking a cell edits it in place; a formula starts "
         "with an equals sign. Pressing Enter or clicking another cell commits the change; pressing Escape cancels an "
         "uncommitted change. Ordinary cells show the same value in the grid and in the formula bar.")

V141 = {"thing": "cell", "route": "/workbooks/1", "role": "gridcell", "instances": 96,
        "modes": [{"mode": "dblclick", "cell": "E1", "other": "L8", "shown": True, "editor": "Edit E1", "afterClick": True,
                   "persisted": True},
                  {"mode": "type", "cell": "H1", "other": "L8", "shown": True, "editor": "H1", "afterClick": False,
                   "persisted": False, "enter": {"cell": "K1", "shown": True, "persisted": True}}],
        "escapes": [{"mode": "dblclick", "cell": "A1", "old": "Region", "shown": True, "afterEscape": False,
                     "persisted": False, "oldStays": True},
                    {"mode": "type", "cell": "B1", "old": "Sales", "shown": True, "afterEscape": True, "persisted": True,
                     "oldStays": True}],
        "visited": ["/", "/", "/workbooks/1"], "ms": 16432}

def lost(found):
    return [f for f in found if f["problem"].startswith("Typed value lost after clicking another")]

def escaped(found):
    return [f for f in found if f["problem"].startswith("Escape did not cancel")]

def statement():
    from agent.commit_probe import commit_statements
    said = commit_statements(SHEET)
    assert len(said) == 1, said
    return said[0]

def with_mode(index, **changes):
    result = json.loads(json.dumps(V141))
    result["modes"][index].update(changes)
    return result

@case
def the_commit_statement_is_recognised_positively_only():
    from agent.commit_probe import commit_statements, probe_job
    said = statement()
    assert said["thing"] == "cell" and said["roles"] == ["gridcell", "cell"] and said["escape"] is True, said
    assert "clicking another cell commits the change" in said["sentence"], said
    assert said["escape_sentence"].startswith("pressing Escape cancels"), said
    assert commit_statements("Selecting a different row saves the edited value.")[0]["roles"] == ["row"]
    for text in ("Clicking another cell does not commit the change.",
                 "Clicking another cell never commits the change.",
                 "Clicking a cell commits nothing until Enter is pressed.",
                 "Clicking a cell commits the change.",
                 "Clicking another cell selects it.",
                 "Escape closes the selector; for an unmatched search it preserves the text.",
                 ""):
        assert commit_statements(text) == [], text
    unknown = commit_statements("Clicking another tab commits the change.")
    assert unknown and unknown[0]["roles"] == [] and probe_job(unknown[0], []) is None, unknown
    assert commit_statements("Clicking another cell commits the change.")[0]["escape"] is False

    assert commit_statements("Clicking another cell commits the change. Escape closes the menu.")[0]["escape"] is False

@case
def the_job_takes_snapshot_routes_that_show_the_role():
    from agent.commit_probe import probe_job
    snapshot = [{"route": "/", "aria": '- link "Q3 Sales"'},
                {"route": "/workbooks/1", "aria": '- grid "Sheet":\n  - row "1":\n    - gridcell "A1"'},
                {"route": "/workbooks/:id", "aria": '- gridcell "A1"'},
                {"route": "/broken", "aria": '- gridcell "A1"', "error": "500"}]
    job = probe_job(statement(), snapshot)
    assert job == {"thing": "cell", "roles": ["gridcell", "cell"], "escape": True, "routes": ["/workbooks/1"]}, job
    assert probe_job(statement(), None)["routes"] == []

@case
def the_v141_replay_reports_the_lost_value_and_the_escape():
    from agent.commit_probe import commit_findings
    found = commit_findings(statement(), V141, log=QUIET)
    assert len(found) == 2, found
    assert "“H1”" in lost(found)[0]["problem"] and "clicked it and typed" in lost(found)[0]["problem"], found
    assert "(it was already gone before the reload)" in lost(found)[0]["problem"]
    assert "“K1”" in lost(found)[0]["problem"] and "onMouseDown" in lost(found)[0]["fix"]
    problem = escaped(found)[0]["problem"]
    assert "“B1”" in problem and "clicked it and typed" in problem and "only typing straight" in problem, problem
    assert all(f["where"] == "route /workbooks/1" for f in found)

@case
def a_lost_value_is_reported_only_on_the_contrast():
    from agent.commit_probe import commit_findings
    said = statement()
    silent = [
        with_mode(1, enter={"cell": "K1", "shown": True, "persisted": False}),
        with_mode(1, enter={"cell": "K1", "shown": False}),
        with_mode(1, enter=None),
        with_mode(1, shown=False),
        with_mode(1, persisted=True),
        with_mode(1, persisted=None),
        with_mode(1, error="the other instance is covered"),
        {"error": "no page shows 3 visible instances of gridcell/cell", "modes": [], "escapes": []},
        None,
        "garbage",
    ]
    for result in silent:
        assert lost(commit_findings(said, result, log=QUIET)) == [], result
    logged = []
    commit_findings(said, with_mode(1, enter=None), log=logged.append)
    assert any("diagnostic, not reported" in line for line in logged), logged

@case
def escape_is_reported_only_when_said_and_the_typed_value_was_saved():
    from agent.commit_probe import commit_findings
    said = statement()
    no_escape = dict(said, escape=False)
    assert escaped(commit_findings(no_escape, V141, log=QUIET)) == []
    for change in ({"persisted": False}, {"persisted": None}, {"shown": False}):
        result = json.loads(json.dumps(V141))
        result["escapes"][1].update(change)
        assert escaped(commit_findings(said, result, log=QUIET)) == [], change
    only_dbl = json.loads(json.dumps(V141))
    only_dbl["escapes"] = [dict(V141["escapes"][0], persisted=True)]
    problem = escaped(commit_findings(said, only_dbl, log=QUIET))[0]["problem"]
    assert "double-clicked it and typed" in problem and "only typing straight" not in problem, problem
    reverse = json.loads(json.dumps(V141))
    reverse["escapes"] = [dict(V141["escapes"][0], persisted=True), dict(V141["escapes"][1], persisted=False)]
    problem = escaped(commit_findings(said, reverse, log=QUIET))[0]["problem"]
    assert "double-clicked it and typed" in problem and "only typing straight" not in problem, problem
    replaced = json.loads(json.dumps(V141))
    replaced["escapes"][1]["oldStays"] = False
    assert "“Sales” was gone" in escaped(commit_findings(said, replaced, log=QUIET))[0]["problem"]
    assert escaped(commit_findings(said, dict(V141, escapes=None), log=QUIET)) == []

def audit(whole: str, logs: list | None = None) -> list[dict[str, str]]:
    from agent.detaudit import deterministic_findings
    node = {"id": "REQ-3-1-1", "name": "Edit cells", "description": "Edit a cell.", "requirement_text": whole}
    snapshot = [{"route": "/workbooks/1", "url": "http://127.0.0.1:3300/workbooks/1", "links": [],
                 "aria": '- grid "Sheet":\n  - row "1":\n    - gridcell "A1"\n    - gridcell "B1"'}]
    with tempfile.TemporaryDirectory() as tmp:
        return deterministic_findings(Path(tmp), node, snapshot, log=(logs.append if logs is not None else QUIET))

@case
def the_audit_runs_the_probe_only_with_a_statement_and_survives_it():
    import agent.detaudit as detaudit
    calls = []

    def fake_probe(project_dir, job, log=print):
        calls.append(job)
        return V141

    def crashing_probe(project_dir, job, log=print):
        raise RuntimeError("boom")

    real = detaudit.commit_probe
    saved = os.environ.get("ARC_COMMIT_PROBE")
    detaudit.commit_probe = fake_probe
    try:
        os.environ.pop("ARC_COMMIT_PROBE", None)
        found = audit(SHEET)
        assert calls == [{"thing": "cell", "roles": ["gridcell", "cell"], "escape": True, "routes": ["/workbooks/1"]}], calls
        assert len(lost(found)) == 1 and len(escaped(found)) == 1, found
        calls.clear()
        assert lost(audit("Double-clicking a cell edits it in place.")) == [] and calls == []
        os.environ["ARC_COMMIT_PROBE"] = "0"
        assert lost(audit(SHEET)) == [] and calls == []
        os.environ.pop("ARC_COMMIT_PROBE", None)
        detaudit.commit_probe = crashing_probe
        logs = []
        assert lost(audit(SHEET, logs)) == []
        assert any("commit probe crashed" in line for line in logs), logs
    finally:
        detaudit.commit_probe = real
        if saved is None:
            os.environ.pop("ARC_COMMIT_PROBE", None)
        else:
            os.environ["ARC_COMMIT_PROBE"] = saved

    assert lost(audit(SHEET)) == []

FIXTURE_SERVER = r"""
const http = require('http');
const MODE = process.env.FIXTURE_MODE === 'correct' ? 'correct' : 'broken';
const cells = { A1: 'Region', B1: 'Sales', C1: 'Units' };
const PAGE = `<!doctype html><html><head><meta charset="utf-8"><title>Grid</title><style>
body{font:14px sans-serif;margin:16px} [role=row]{display:flex} .cell{width:90px;height:28px;border:1px solid #999;
box-sizing:border-box;padding:2px;overflow:hidden} .cell.sel{outline:2px solid #36c} .cell input{width:100%;height:100%;
border:0;padding:0;font:inherit;box-sizing:border-box}</style></head><body><h1>Sheet</h1>
<div role="grid" aria-label="Sheet" id="g"></div><script>
const MODE = '${MODE}';
let cells = {}; let sel = null; let edit = null;
const ids = []; for (const r of [1, 2, 3, 4, 5, 6, 7]) for (const c of 'ABCD') ids.push(c + r);
const save = (id, value) => fetch('/api/cells/' + id, { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ value }) });
const commit = () => { const e = edit; edit = null; if (e) { cells[e.id] = e.draft; save(e.id, e.draft); } render(); };
const cancel = () => { edit = null; render(); };
function render() {
  const g = document.getElementById('g'); g.innerHTML = '';
  for (let r = 0; r < 7; r++) {
    const row = document.createElement('div'); row.setAttribute('role', 'row'); g.appendChild(row);
    for (const id of ids.slice(r * 4, r * 4 + 4)) {
      const cell = document.createElement('div'); cell.setAttribute('role', 'gridcell'); cell.setAttribute('aria-label', id);
      cell.className = 'cell' + (sel === id ? ' sel' : ''); cell.dataset.id = id; row.appendChild(cell);
      if (edit && edit.id === id) {
        const input = document.createElement('input'); input.value = edit.draft; input.setAttribute('aria-label', 'Edit ' + id);
        input.addEventListener('input', () => { edit.draft = input.value; });
        input.addEventListener('keydown', (ev) => {
          if (ev.key === 'Enter') { ev.preventDefault(); commit(); }
          else if (ev.key === 'Escape') { ev.preventDefault(); if (MODE === 'broken' && edit.how === 'type') commit(); else cancel(); }
        });
        cell.appendChild(input); setTimeout(() => { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }, 0);
      } else cell.textContent = cells[id] || '';
    }
  }
}
document.addEventListener('mousedown', (ev) => {
  const cell = ev.target.closest && ev.target.closest('[role=gridcell]');
  if (cell) sel = cell.dataset.id;
  if (edit && !(cell && cell.dataset.id === edit.id)) {
    // broken like sheet v141: a typed-into cell is re-rendered away before its text is read; a double-click edit commits
    if (MODE === 'correct' || edit.how === 'dbl') commit(); else cancel();
    return;
  }
  // a plain selection change moves the outline only (a re-render here would keep the browser from seeing a double-click)
  document.querySelectorAll('[role=gridcell]').forEach((c) => c.classList.toggle('sel', c.dataset.id === sel));
}, true);
document.addEventListener('dblclick', (ev) => {
  const cell = ev.target.closest && ev.target.closest('[role=gridcell]');
  if (cell) { edit = { id: cell.dataset.id, draft: cells[cell.dataset.id] || '', how: 'dbl' }; render(); }
});
document.addEventListener('keydown', (ev) => {
  if (edit || !sel || ev.key.length !== 1 || ev.ctrlKey || ev.metaKey || ev.altKey) return;
  ev.preventDefault();
  edit = { id: sel, draft: MODE === 'broken' ? (cells[sel] || '') + ev.key : ev.key, how: 'type' }; render();
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

def browser_fixture(mode: str, backend: Path) -> list[dict[str, str]]:
    from agent.commit_probe import commit_findings, probe_job, run_probe
    said = statement()
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
            result = run_probe(backend, f"http://127.0.0.1:{port}", probe_job(said, None), log=print)
            print(f"  {mode}: {json.dumps(result)[:700]}")
            assert isinstance(result, dict) and not result.get("error"), result
            return commit_findings(said, result, log=QUIET)
        finally:
            server.kill()
            server.wait(timeout=10)

def browser_main() -> int:
    backend = Path(os.environ.get("ARC_PLAYWRIGHT_BACKEND") or ROOT / "template" / "backend")
    if not (backend / "node_modules" / "@playwright" / "test").is_dir():
        print(f"skipped  browser fixtures: no node_modules/@playwright/test under {backend.name} (set ARC_PLAYWRIGHT_BACKEND)")
        return 0
    failed = 0
    for mode, want in (("broken", (1, 1)), ("correct", (0, 0))):
        try:
            found = browser_fixture(mode, backend)
            got = (len(lost(found)), len(escaped(found)))
            assert got == want and len(found) == sum(want), (mode, got, found)
            print(f"ok    browser fixture {mode}: lost={got[0]} escape={got[1]}")
        except Exception:
            failed += 1
            print(f"FAIL  browser fixture {mode}")
            traceback.print_exc(limit=4)
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
    print(f"{len(CASES) - failed}/{len(CASES)} commitprobe cases passed")
    if "--browser" in sys.argv:
        failed += browser_main()
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
