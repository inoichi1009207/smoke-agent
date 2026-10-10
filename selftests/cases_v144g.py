from __future__ import annotations

import copy
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

SHEET = ("After selecting a cell in the current active worksheet, users can modify its content directly in the grid or "
         "formula bar. Pressing Enter or clicking another cell commits the change; pressing Escape cancels an uncommitted "
         "change.")

PLATFORM = {"thing": "cell", "route": "/workbooks/1", "role": "gridcell", "instances": 96, "modes": [], "escapes": [],
            "keys": {"enter": {"mode": "type", "cell": "D1", "below": "D2", "shown": True, "movedDown": False, "stayed": True,
                               "stored": True},
                     "tab": {"mode": "type", "cell": "F1", "right": "G1", "shown": True, "movedRight": False, "stayed": True,
                             "active": "F1", "inGrid": True, "typedNext": True, "stored": True, "merged": True,
                             "nextInA": True, "nextInRight": False, "after": "zq91243k22zq91243k21"},
                     "arrow": {"mode": "type", "key": "ArrowDown", "cell": "H1", "below": "H2", "shown": True,
                               "movedDown": True, "stayed": False, "shownAfter": True, "shownSettled": True, "stored": False},
                     "replace": {"mode": "type", "cell": "A1", "old": "Region", "shown": True, "stored": True, "oldKept": True,
                                 "after": "Regiozq97579k24n"}, "ms": 11670}}
LOCAL = {"thing": "cell", "route": "/workbooks/1", "role": "gridcell", "instances": 96, "modes": [], "escapes": [],
         "keys": {"enter": {"mode": "type", "cell": "E1", "below": "E2", "shown": True, "movedDown": False, "stayed": True,
                            "stored": True},
                  "tab": {"mode": "type", "cell": "G1", "right": "H1", "shown": True, "movedRight": False, "stayed": True,
                          "active": "H1", "inGrid": True, "typedNext": True, "stored": True, "merged": False, "nextInA": False,
                          "nextInRight": True, "after": "zq34171k21"},
                  "arrow": {"mode": "type", "key": "ArrowDown", "cell": "I1", "below": "I2", "shown": True, "movedDown": False,
                            "stayed": True, "shownAfter": True, "shownSettled": True, "stored": False},
                  "replace": {"mode": "type", "cell": "A1", "old": "Region", "shown": True, "stored": True, "oldKept": True,
                              "after": "Regiozq40181k24n"}, "ms": 11134}}
V138 = {"thing": "cell", "route": "/workbooks/1", "role": "gridcell", "instances": 96, "modes": [], "escapes": [],
        "keys": {"enter": {"mode": "dblclick", "cell": "J1", "below": "J2", "shown": True, "movedDown": False, "stayed": True,
                           "stored": True},
                 "tab": {"mode": "dblclick", "cell": "L1", "right": "M1", "shown": True, "movedRight": False, "stayed": True,
                         "active": "button", "inGrid": False, "stored": True, "merged": False, "nextInA": False,
                         "nextInRight": False, "after": "zq56359k21"}, "ms": 7157}}

def said(text=SHEET):
    from agent.commit_probe import key_statements
    return key_statements(text)

def kinds(found):
    out = []
    for f in found:
        p = f["problem"]
        out.append("tab-merged" if p.startswith("Tab merged") else "tab-lost" if p.startswith("Tab lost")
                   else "tab-moves" if p.startswith("Tab did not move") else "arrow" if p.startswith("An arrow key")
                   else "arrow-moves" if p.startswith("ArrowDown did not") else "enter" if p.startswith("Enter did not commit")
                   else "enter-moves" if p.startswith("Enter did not move") else "replace" if p.startswith("Typing over")
                   else "?")
    return sorted(out)

def findings(statement, result):
    from agent.commit_probe import keys_findings
    return keys_findings(statement, result, log=QUIET)

@case
def key_statements_are_recognised_positively_only():
    assert said() == {"thing": "cell", "roles": ["gridcell", "cell"],
                      "said": {"enter_commits": "Pressing Enter or clicking another cell commits the change;"}}, said()
    got = said("Pressing Tab commits the cell and moves to the next cell on the right. Arrow keys move the selection "
               "between cells. Selecting a cell and typing replaces its content. Enter moves down to the cell below.")
    assert sorted(got["said"]) == ["arrow_moves", "enter_moves", "replace", "tab_commits", "tab_moves"], got
    for text in ("The active tab moves to the new worksheet cell.",
                 "Clicking a sheet tab commits the cell.",
                 "Pressing Enter does not commit the cell.",
                 "Pressing Enter in the dialog commits the cell value.",
                 "Pressing Enter commits the change.",
                 "Typing in the search box replaces the results.",
                 ""):
        assert said(text) is None, (text, said(text))

@case
def the_keys_job_needs_a_statement_and_leaves_the_commit_job_alone():
    from agent.commit_probe import commit_statements, keys_job, probe_job
    snapshot = [{"route": "/workbooks/1", "aria": '- grid "Sheet":\n  - row "1":\n    - gridcell "A1"'}]
    assert keys_job(None, snapshot) is None and keys_job(said("No keys here."), snapshot) is None
    job = keys_job(said(), snapshot)
    assert job == {"thing": "cell", "roles": ["gridcell", "cell"], "escape": False, "routes": ["/workbooks/1"],
                   "keys": ["enter_commits"]}, job
    commit = probe_job(commit_statements(SHEET)[0], snapshot)
    assert "keys" not in commit and commit["escape"] is True, commit

@case
def the_platform_replay_reports_tab_merged_and_the_arrow_ghost_only():
    found = findings(said(), PLATFORM)
    assert kinds(found) == ["arrow", "tab-merged"], kinds(found)
    tab = next(f for f in found if f["problem"].startswith("Tab merged"))
    assert "contrast" in tab["requirement"] and "“D1”" in tab["requirement"] and "zq91243k22zq91243k21" in tab["problem"], tab
    arrow = next(f for f in found if f["problem"].startswith("An arrow key"))
    assert "kept SHOWING" in arrow["problem"] and "“H2”" in arrow["problem"], arrow
    assert all(set(f) == {"where", "requirement", "problem", "fix"} and f["where"] == "route /workbooks/1" for f in found)

@case
def unstated_expectations_are_reported_only_once_stated():
    stated = said(SHEET + " Selecting a cell and typing replaces its content. Enter moves down to the cell below.")
    assert kinds(findings(stated, PLATFORM)) == ["arrow", "enter-moves", "replace", "tab-merged"], kinds(findings(stated, PLATFORM))
    assert kinds(findings(stated, LOCAL)) == ["enter-moves", "replace"], kinds(findings(stated, LOCAL))
    tab_said = said("Pressing Tab moves to the next cell on the right.")
    assert kinds(findings(tab_said, LOCAL)) == ["tab-moves"], kinds(findings(tab_said, LOCAL))

@case
def no_contrast_no_finding():

    broken = copy.deepcopy(PLATFORM)
    broken["keys"]["enter"]["stored"] = False
    assert kinds(findings(said(), broken)) == ["enter"], kinds(findings(said(), broken))
    assert findings(said("Clicking another cell commits the change."), broken) == []

    still = copy.deepcopy(PLATFORM)
    still["keys"]["arrow"].update(movedDown=False, stayed=True)
    assert kinds(findings(said(), still)) == ["tab-merged"], kinds(findings(said(), still))

    fine = copy.deepcopy(PLATFORM)
    fine["keys"]["tab"].update(merged=False, nextInA=False, nextInRight=True, after="zq91243k21")
    assert kinds(findings(said(), fine)) == ["arrow"], kinds(findings(said(), fine))

    lost = copy.deepcopy(fine)
    lost["keys"]["tab"].update(stored=False, after="")
    assert kinds(findings(said(), lost)) == ["arrow", "tab-lost"], kinds(findings(said(), lost))

    assert findings(said(), LOCAL) == [] and findings(said(), V138) == []
    assert findings(said(), None) == [] and findings(None, PLATFORM) == [] and findings(said(), {"error": "x"}) == []

@case
def enter_not_committing_is_reported_only_when_stated_and_the_grid_does_save():
    broken = copy.deepcopy(LOCAL)
    broken["keys"]["enter"]["stored"] = False
    assert kinds(findings(said(), broken)) == ["enter"], kinds(findings(said(), broken))
    nothing = copy.deepcopy(broken)
    for name in ("tab", "arrow", "replace"):
        nothing["keys"][name]["stored"] = False
        nothing["keys"][name]["merged"] = False
    assert findings(said(), nothing) == [], findings(said(), nothing)

@case
def the_keys_part_is_switched_and_gated():
    from agent import commit_probe as cp
    saved = {k: os.environ.get(k) for k in ("ARC_COMMIT_KEYS", "ARC_COMMIT_PROBE")}
    try:
        for k in saved:
            os.environ.pop(k, None)
        assert cp.keys_enabled() is True
        os.environ["ARC_COMMIT_KEYS"] = "0"
        assert cp.keys_enabled() is False and cp.enabled() is True
        os.environ.pop("ARC_COMMIT_KEYS")
        os.environ["ARC_COMMIT_PROBE"] = "0"
        assert cp.keys_enabled() is False
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    script = cp.PROBE_SCRIPT
    assert "const doCommit = !job.keys;" in script and "doCommit ? ['dblclick', 'type'] : []" in script
    assert "job.escape && doCommit" in script and "if (job.keys) {" in script

    with tempfile.TemporaryDirectory() as tmp:
        assert cp.keys_probe(Path(tmp), cp.keys_job(said(), None), log=QUIET) is None

FIXTURE_SERVER = r"""
const http = require('http');
const MODE = process.env.FIXTURE_MODE === 'correct' ? 'correct' : 'broken';
const cells = { A1: 'Region', B1: 'Sales', C1: 'Units' };
const PAGE = `<!doctype html><html><head><meta charset="utf-8"><title>Grid</title><style>
body{font:14px sans-serif;margin:16px} [role=row]{display:flex} .cell{width:90px;height:28px;border:1px solid #999;
box-sizing:border-box;padding:2px;overflow:hidden} .cell[aria-selected=true]{outline:2px solid #36c} .cell input{width:100%;
height:100%;border:0;padding:0;font:inherit;box-sizing:border-box}</style></head><body><h1>Sheet</h1>
<div role="grid" aria-label="Sheet" id="g" tabindex="0"></div><script>
const MODE = '${MODE}';
const COLS = 'ABCD', ROWS = 7;
let cells = {}; let sel = null; let edit = null; const ghost = {};
const ids = []; for (let r = 1; r <= ROWS; r++) for (const c of COLS) ids.push(c + r);
const save = (id, value) => fetch('/api/cells/' + id, { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ value }) });
const step = (id, dc, dr) => { const c = COLS.indexOf(id[0]) + dc, r = Number(id.slice(1)) + dr;
  return c >= 0 && c < COLS.length && r >= 1 && r <= ROWS ? COLS[c] + r : id; };
const commit = () => { const e = edit; edit = null; if (e) { cells[e.id] = e.draft; save(e.id, e.draft); } };
function render() {
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
        const at = edit.caret === 'start' ? 0 : input.value.length;
        setTimeout(() => { input.focus(); input.setSelectionRange(at, at); }, 0);
      } else cell.textContent = ghost[id] !== undefined ? ghost[id] : (cells[id] || '');
    }
  }
  if (!edit) document.getElementById('g').focus();
}
document.addEventListener('mousedown', (ev) => {
  const cell = ev.target.closest && ev.target.closest('[role=gridcell]');
  if (edit && !(cell && cell.dataset.id === edit.id)) commit();
  if (cell) { sel = cell.dataset.id; ev.preventDefault(); render(); }
}, true);
document.addEventListener('keydown', (ev) => {
  if (!sel) return;
  if (ev.key === 'Enter') { ev.preventDefault(); if (edit) { commit(); if (MODE === 'correct') sel = step(sel, 0, 1); render(); } return; }
  if (ev.key === 'Tab') {
    ev.preventDefault();
    if (edit) { commit(); if (MODE === 'broken') { edit = { id: sel, draft: cells[sel], caret: 'start' }; render(); return; } }
    sel = step(sel, 1, 0); render(); return;
  }
  if (ev.key === 'ArrowDown') {
    ev.preventDefault();
    if (edit && MODE === 'broken') { ghost[edit.id] = edit.draft; edit = null; }
    else if (edit) commit();
    sel = step(sel, 0, 1); render(); return;
  }
  if (edit || ev.key.length !== 1 || ev.ctrlKey || ev.metaKey || ev.altKey) return;
  ev.preventDefault();
  edit = { id: sel, draft: MODE === 'broken' ? (cells[sel] || '') + ev.key : ev.key }; render();
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

def browser_fixture(mode: str, backend: Path, statement) -> list[dict[str, str]]:
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
            result = run_probe(backend, f"http://127.0.0.1:{port}", keys_job(statement, None), log=print)
            print(f"  {mode}: {json.dumps(result)[:900]}")
            assert isinstance(result, dict) and not result.get("error") and isinstance(result.get("keys"), dict), result
            return findings(statement, result)
        finally:
            server.kill()
            server.wait(timeout=10)

def browser_main() -> int:
    backend = Path(os.environ.get("ARC_PLAYWRIGHT_BACKEND") or ROOT / "template" / "backend")
    if not (backend / "node_modules" / "@playwright" / "test").is_dir():
        print(f"skipped  browser fixtures: no node_modules/@playwright/test under {backend.name} (set ARC_PLAYWRIGHT_BACKEND)")
        return 0
    failed = 0
    everything = said(SHEET + " Selecting a cell and typing replaces its content. Enter moves down to the cell below. "
                              "Pressing Tab moves to the next cell on the right.")
    for mode, statement, want in (("broken", said(), ["arrow", "tab-merged"]),
                                  ("broken", everything, ["arrow", "enter-moves", "replace", "tab-merged"]),
                                  ("correct", everything, [])):
        try:
            got = kinds(browser_fixture(mode, backend, statement))
            assert got == want, (mode, got, want)
            print(f"ok    browser fixture {mode} ({sorted(statement['said'])}): {got}")
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
    print(f"{len(CASES) - failed}/{len(CASES)} v144g cases passed")
    if "--browser" in sys.argv:
        failed += browser_main()
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
