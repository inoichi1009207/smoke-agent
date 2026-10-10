from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import traceback
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import structure_probe as sp

CASES = []

def case(function):
    CASES.append(function)
    return function

REQ = ('The home page provides a button with the accessible name "New blank workbook". The editor shows a formula bar '
       'with the accessible name "Formula bar". The row-number menu provides "Insert 1 row above" and "Delete row". '
       'On insertion, the target row and all subsequent complete records, validation rules, and formula references shift '
       'downward together; affected formulas display the adjusted original formulas and correct results, while direct '
       'references that cannot be preserved display #REF!; Supports relative and absolute references.')

@case
def probe_types_into_the_bar_only_when_editable_and_falls_back_to_the_cell_editor():
    script = sp.PROBE_SCRIPT
    enter = script[script.index("const enter = async"):script.index("const blank = async")]
    assert "isEditable()" in enter and "if (editable) {" in enter, enter
    assert "(await textAt(p)) !== ''" in enter and "await viaCell(p, value);" in enter, enter
    assert "out.entry[coord] = 'bar'" in enter and "'read-only bar, cell editor'" in enter, enter
    assert "entry: {}" in script

EMPTY = {"A1": {"text": "", "formula": ""}, "A2": {"text": "", "formula": ""}, "A3": {"text": "", "formula": ""},
         "D3": {"text": "", "formula": ""}}

def _diag(result: dict) -> list[str]:
    said = sp.structure_statement(REQ)
    job = sp.probe_job(said, None)
    lines: list[str] = []
    assert sp.structure_findings(said, job, result, log=lines.append) == []
    return [line for line in lines if "setup not verified" in line]

@case
def setup_diagnostic_says_how_each_cell_was_entered():
    result = {"route": "/w/1", "created": "New blank workbook", "bar": True, "barEditable": False, "setup": EMPTY,
              "entry": {c: "read-only bar, cell editor" for c in ("A1", "A2", "A3", "D1", "D2", "D3")}}
    lines = _diag(result)
    assert len(lines) == 1, lines
    payload = json.loads(lines[0].split("setup not verified:", 1)[1])
    assert payload["barEditable"] is False and payload["entry"]["A1"] == "read-only bar, cell editor", payload
    assert payload["setup"] == EMPTY, payload

@case
def old_results_without_entry_log_as_before():
    lines = _diag({"route": "/w/1", "created": "New blank workbook", "bar": True, "setup": EMPTY})
    assert len(lines) == 1 and '"entry"' not in lines[0] and '"barEditable"' not in lines[0], lines
    assert lines[0].split("setup not verified:", 1)[1].startswith('{"error": null, "created": "New blank workbook", "bar": true, "setup"'), lines

PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><title>grid</title>
<style>td,th{width:60px;height:24px;border:1px solid #ccc;font:14px sans-serif;padding:0}td input{width:56px}</style></head>
<body><button id="new">New blank workbook</button>
<div id="app" hidden><input aria-label="Formula bar" id="bar"><table role="grid" id="grid"></table>
<div role="menu" id="menu" hidden style="position:absolute;background:#fff;border:1px solid #888"></div></div>
<script>
const MODE = '__MODE__';
const COLS = 'ABCDEF', ROWS = 8;
let data = {}, sel = null;
const bar = document.getElementById('bar'), grid = document.getElementById('grid'), menu = document.getElementById('menu');
if (MODE.startsWith('readonly')) bar.readOnly = true;
const REF = /(\$?)([A-F])(\$?)(\d+)/g;
function num(coord, depth) {
  const raw = data[coord];
  if (raw === undefined || raw === '') return 0;
  if (String(raw).startsWith('=')) return calc(raw, depth + 1);
  const n = Number(raw); return isNaN(n) ? NaN : n;
}
function calc(raw, depth) {
  if (depth > 20 || raw.includes('#REF!')) return '#REF!';
  let e = raw.slice(1).replace(/SUM\(\$?([A-F])\$?(\d+):\$?([A-F])\$?(\d+)\)/g, (m, c1, r1, c2, r2) => {
    let s = 0;
    for (let c = COLS.indexOf(c1); c <= COLS.indexOf(c2); c++) for (let r = +r1; r <= +r2; r++) s += num(COLS[c] + r, depth);
    return '(' + s + ')';
  });
  e = e.replace(REF, (m, a, c, b, r) => '(' + num(c + r, depth) + ')');
  if (!/^[0-9+\-*\/(). ]*$/.test(e)) return '#ERROR!';
  try { const v = Function('return (' + e + ')')(); return Number.isFinite(v) ? v : '#DIV/0!'; } catch (x) { return '#ERROR!'; }
}
function shown(coord) { const raw = data[coord]; if (raw === undefined) return ''; return String(raw).startsWith('=') ? String(calc(raw, 0)) : raw; }
function render() {
  let h = '<tr role="row"><th></th>' + [...COLS].map((c) => '<th role="columnheader">' + c + '</th>').join('') + '</tr>';
  for (let r = 1; r <= ROWS; r++) {
    h += '<tr role="row"><th role="rowheader" data-row="' + r + '">' + r + '</th>';
    for (const c of COLS) h += '<td role="gridcell" aria-label="' + c + r + '" data-c="' + c + r + '">' + shown(c + r) + '</td>';
    h += '</tr>';
  }
  grid.innerHTML = h;
  bar.value = sel ? (data[sel] || '') : '';
}
function shift(at, by) {  // rows >= at move by `by`; by -1 deletes row `at`
  const next = {};
  for (const [k, v] of Object.entries(data)) {
    const c = k[0], r = +k.slice(1);
    if (by < 0 && r === at) continue;
    next[r >= at ? c + (r + by) : k] = v;
  }
  for (const [k, v] of Object.entries(next)) {
    if (!String(v).startsWith('=')) continue;
    next[k] = v.replace(REF, (m, a, c, b, r) => {
      r = +r;
      if (by < 0 && r === at) return MODE.endsWith('repoint') ? a + c + b + Math.max(1, r - 1) : '#REF!';
      return a + c + b + (r >= at ? r + by : r);
    });
  }
  data = next; render();
}
document.getElementById('new').onclick = () => { data = {}; sel = null; document.getElementById('app').hidden = false; render(); };
grid.addEventListener('click', (ev) => { const td = ev.target.closest('td'); if (!td || td.querySelector('input')) return; sel = td.dataset.c; bar.value = data[sel] || ''; });
grid.addEventListener('dblclick', (ev) => {
  const td = ev.target.closest('td'); if (!td) return;
  sel = td.dataset.c; const input = document.createElement('input'); input.value = data[sel] || '';
  td.textContent = ''; td.appendChild(input); input.focus();
  input.addEventListener('keydown', (k) => { if (k.key === 'Enter') { data[td.dataset.c] = input.value; render(); } });
});
bar.addEventListener('keydown', (k) => { if (k.key === 'Enter' && MODE === 'editable' && sel) { data[sel] = bar.value; render(); } });
grid.addEventListener('contextmenu', (ev) => {
  const th = ev.target.closest('th[role=rowheader]'); if (!th) return;
  ev.preventDefault(); const r = +th.dataset.row;
  menu.innerHTML = '<button role="menuitem" id="ins">Insert 1 row above</button><button role="menuitem" id="del">Delete row</button>';
  menu.style.left = ev.pageX + 'px'; menu.style.top = ev.pageY + 'px'; menu.hidden = false;
  document.getElementById('ins').onclick = () => { menu.hidden = true; shift(r, 1); };
  document.getElementById('del').onclick = () => { menu.hidden = true; shift(r, -1); };
});
</script></body></html>"""

def _modules() -> Path | None:
    for candidate in (os.environ.get("ARC_TX_NODE_MODULES", ""), str(ROOT / "template" / "backend" / "node_modules")):
        if candidate and (Path(candidate) / "@playwright" / "test").is_dir():
            return Path(candidate)
    return None

def _live(mode: str) -> tuple[dict, list[dict], list[str]]:
    modules = _modules()
    if modules is None or shutil.which("node") is None:
        raise unittest.SkipTest("no node or no @playwright/test (set ARC_TX_NODE_MODULES)")
    body = PAGE.replace("__MODE__", mode).encode("utf-8")

    class Page(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        said = sp.structure_statement(REQ)
        job = sp.probe_job(said, None)
        lines: list[str] = []
        result = sp.run_probe(modules.parent, f"http://127.0.0.1:{server.server_address[1]}", job, log=lines.append)
        assert isinstance(result, dict), lines
        found = sp.structure_findings(said, job, result, log=lines.append)
        return result, found, lines
    finally:
        server.shutdown()
        server.server_close()

def _setup_verified(result: dict, lines: list[str]) -> None:
    assert result.get("created") == "New blank workbook", result
    assert result["setup"]["A2"] == {"text": "20", "formula": "=A1*10"}, result["setup"]
    assert result["setup"]["A3"] == {"text": "3", "formula": "=$A$1+1"}, result["setup"]
    assert not any("setup not verified" in line for line in lines), lines

@case
def live_read_only_bar_correct_app_setup_verified_and_no_finding():
    result, found, lines = _live("readonly")
    _setup_verified(result, lines)
    assert result.get("barEditable") is False and set(result["entry"].values()) == {"read-only bar, cell editor"}, result
    assert result["afterDelete"]["A2"]["formula"] == "=#REF!*10", result["afterDelete"]
    assert found == [], found

@case
def live_read_only_bar_repointed_delete_is_reported():
    result, found, lines = _live("readonly-repoint")
    _setup_verified(result, lines)
    assert result["afterDelete"]["A2"]["formula"] == "=A1*10", result["afterDelete"]
    assert [f["problem"][:48] for f in found] == ["Reference to a deleted row silently re-pointed: "], found

@case
def live_bar_that_takes_nothing_is_repeated_in_the_cell_editor():
    result, found, lines = _live("inert")
    _setup_verified(result, lines)
    assert "barEditable" not in result and set(result["entry"].values()) == {"bar took nothing, cell editor"}, result
    assert found == [], found

@case
def live_committing_bar_is_still_used():
    result, found, lines = _live("editable")
    _setup_verified(result, lines)
    assert set(result["entry"].values()) == {"bar"}, result
    assert found == [], found

def main() -> int:
    if not __debug__:
        print("FAIL  self-tests need assertions: run without -O and without PYTHONOPTIMIZE")
        return 1
    failed = skipped = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except unittest.SkipTest as skip:
            skipped += 1
            print(f"SKIP  {function.__name__}: {skip}")
        except Exception:
            failed += 1
            print(f"FAIL  {function.__name__}")
            traceback.print_exc(limit=6)
    print(f"{len(CASES) - failed - skipped}/{len(CASES)} v146d cases passed ({skipped} skipped)")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
