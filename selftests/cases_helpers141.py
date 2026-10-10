from __future__ import annotations

import json
import re
import shutil
import subprocess
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

def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)

PROBE = r"""
const H = require('./outsider.js');
const KEYS = new Set(['Enter', 'Escape', 'Control', 'Shift', 'Alt', 'Meta', 'ControlOrMeta', 'Z', 'V', 'A', 'a', 'c', '0', '1', '+', '=', 'ArrowDown', 'F2']);
function loc(n) {
  const l = { count: async () => n, nth: () => ({ isVisible: async () => true, isEnabled: async () => true, isEditable: async () => true,
      evaluate: async () => '' }),
    getByText: () => loc(0), getByRole: () => loc(0), getByPlaceholder: () => loc(0), getByLabel: () => loc(0), getByTitle: () => loc(0),
    locator: () => loc(0), filter() { return l; }, last() { return l; }, first() { return l; }, or() { return l; }, and() { return l; } };
  return l;
}
function fakePage(items, cells, keyboardError) {
  const pressed = [];
  const page = {
    pressed,
    url: () => 'http://app.test/',
    on() {},
    evaluate: async () => { throw new Error('no document'); },
    locator: () => ({ filter: ({ hasText }) => ({ last: () => loc(items.filter((t) => hasText.test(t)).length) }) }),
    getByRole: (role, o) => loc(role === 'gridcell' && o && o.name ? cells.filter((c) => o.name.test(c)).length : 0),
    getByText: () => loc(0), getByPlaceholder: () => loc(0), getByLabel: () => loc(0), getByTitle: () => loc(0),
    keyboard: { press: async (k) => {
      pressed.push(k);  // what REACHED the keyboard (Playwright holds the modifiers down before it sees a bad key)
      if (keyboardError) throw new Error(keyboardError);
      const tokens = []; let b = '';  // Playwright's split
      for (const ch of k) { if (ch === '+' && b) { tokens.push(b); b = ''; } else b += ch; }
      tokens.push(b);
      for (const t of tokens) if (!KEYS.has(t)) throw new Error(`keyboard.press: Unknown key: "${t}"`);
    } },
  };
  return page;
}
async function outcome(run) {
  try { await run(); return 'ok'; } catch (e) { return String(e && e.message).split('\n')[0]; }
}
(async () => {
  const r = {};
  const jobs = {
    see_cell: async () => { const p = fakePage([], ['A1']); return outcome(() => H.see(p, 'Region', H.item(p, 'A1'))); },
    click_cell: async () => { const p = fakePage([], ['C12']); return outcome(() => H.click(p, 'Edit', H.item(p, 'C12'))); },
    see_real_item_a1: async () => { const p = fakePage(['A1 Seat'], ['A1']); return outcome(() => H.see(p, 'Region', H.item(p, 'A1'))); },
    see_cell_no_grid: async () => { const p = fakePage([], []); return outcome(() => H.see(p, 'Region', H.item(p, 'A1'))); },
    see_lowercase: async () => { const p = fakePage([], ['a1']); return outcome(() => H.see(p, 'Region', H.item(p, 'a1'))); },
    see_word: async () => { const p = fakePage([], ['Invoice 1042']); return outcome(() => H.see(p, 'Paid', H.item(p, 'Invoice 1042'))); },
    see_no_scope: async () => { const p = fakePage([], ['A1']); return outcome(() => H.see(p, 'Region')); },
    notsee_cell: async () => { const p = fakePage([], ['B2']); return outcome(() => H.notSee(p, 'East', H.item(p, 'B2'))); },
    notsee_real_item: async () => { const p = fakePage(['B2 row'], ['B2']); return outcome(() => H.notSee(p, 'East', H.item(p, 'B2'))); },
  };
  for (const key of ['Enter', 'Control+Z', 'ControlOrMeta+A', 'Control+c', '0', 'a', '+', 'Control++', 'Shift+=', 'F2', 'ArrowDown']) {
    jobs['press ' + key] = async () => { const p = fakePage([], []); const o = await outcome(() => H.press(p, key)); return o + ' | ' + JSON.stringify(p.pressed); };
  }
  for (const key of ['=D1*2', 'Control+=D1', 'hello world', 'Control + Z', 'hello', 'enter', '']) {
    jobs['press ' + key] = async () => { const p = fakePage([], []); const o = await outcome(() => H.press(p, key)); return o + ' | ' + JSON.stringify(p.pressed); };
  }
  jobs.press_other_error = async () => { const p = fakePage([], [], 'Target page, context or browser has been closed'); return outcome(() => H.press(p, 'Enter')); };
  const names = Object.keys(jobs);
  const values = await Promise.all(names.map((n) => jobs[n]()));
  names.forEach((n, i) => { r[n] = values[i]; });
  process.stdout.write(JSON.stringify(r));
})();
"""

def run_probe(helper_js: str) -> dict | None:
    node = shutil.which("node")
    if not node:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        stub = base / "node_modules" / "@playwright" / "test"
        stub.mkdir(parents=True)
        (stub / "package.json").write_text('{"name":"@playwright/test","main":"index.js"}', encoding="utf-8")
        (stub / "index.js").write_text("module.exports = { test: {}, expect: {} };\n", encoding="utf-8")
        (base / "outsider.js").write_text(helper_js, encoding="utf-8")
        (base / "probe.cjs").write_text(PROBE, encoding="utf-8")
        done = subprocess.run([node, "probe.cjs"], cwd=str(base), capture_output=True, text=True, timeout=120,
                              encoding="utf-8", errors="replace")
        assert done.returncode == 0, done.stderr[-1500:]
        return json.loads(done.stdout)

CELL_DEFECT = 'SPEC DEFECT: "{0}" is a grid cell, not a list item: use region(page, "gridcell", "{0}")'
PRESS_DEFECT = 'SPEC DEFECT: press() takes a key such as "Enter" or "Control+Z"; to type text use fill()/type'
VALID_KEYS = ["Enter", "Control+Z", "ControlOrMeta+A", "Control+c", "0", "a", "+", "Control++", "Shift+=", "F2", "ArrowDown"]
TEXT_NOT_KEYS = ["=D1*2", "Control+=D1", "hello world", "Control + Z"]
WORDS_NOT_KEYS = ["hello", "enter", ""]

def verdicts(result: dict) -> list[str]:
    wrong = []

    def want(name, ok, detail=""):
        if not ok:
            wrong.append(f"{name}: {result.get(name)!r} {detail}")

    want("see_cell", result["see_cell"] == CELL_DEFECT.format("A1"))
    want("click_cell", result["click_cell"] == CELL_DEFECT.format("C12"))
    want("see_real_item_a1", "SPEC DEFECT" not in result["see_real_item_a1"] and "nothing visible matches" in result["see_real_item_a1"])
    want("see_cell_no_grid", "SPEC DEFECT" not in result["see_cell_no_grid"] and "nothing visible matches" in result["see_cell_no_grid"])
    want("see_lowercase", "SPEC DEFECT" not in result["see_lowercase"])
    want("see_word", "SPEC DEFECT" not in result["see_word"])
    want("see_no_scope", "SPEC DEFECT" not in result["see_no_scope"])
    want("notsee_cell", result["notsee_cell"] == CELL_DEFECT.format("B2"))
    want("notsee_real_item", result["notsee_real_item"] == "ok")
    for key in VALID_KEYS:
        want(f"press {key}", result[f"press {key}"] == "ok | " + json.dumps([key]), "(a valid key must reach the keyboard unchanged)")
    for key in TEXT_NOT_KEYS:
        want(f"press {key}", result[f"press {key}"].startswith(PRESS_DEFECT) and result[f"press {key}"].endswith("| []"),
             "(text must be refused BEFORE a modifier is held down)")
    for key in WORDS_NOT_KEYS:
        want(f"press {key}", result[f"press {key}"].startswith(PRESS_DEFECT), "(Playwright's Unknown key becomes a spec defect)")
    want("press_other_error", result["press_other_error"].startswith("Target page") and "SPEC DEFECT" not in result["press_other_error"])
    return wrong

_RESULT: dict = {}

def _probe() -> dict | None:
    if "r" not in _RESULT:
        from agent.outsider import HELPER_JS
        _RESULT["r"] = run_probe(HELPER_JS)
    return _RESULT["r"]

@case
def helper_is_valid_javascript():
    node = shutil.which("node")
    if not node:
        return
    from agent.outsider import HELPER_JS
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "outsider.js"
        path.write_text(HELPER_JS, encoding="utf-8")
        done = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=60)
        assert done.returncode == 0, done.stderr[-800:]

def cell_item_is_a_spec_defect_only_when_nothing_was_found():
    result = _probe()
    if result is None:
        return
    wrong = [w for w in verdicts(result) if not w.startswith("press")]
    assert not wrong, wrong

@case
def press_refuses_text_and_keeps_every_key():
    result = _probe()
    if result is None:
        return
    wrong = [w for w in verdicts(result) if w.startswith("press")]
    assert not wrong, wrong

@case
def every_failed_scoped_lookup_passes_its_scope():
    from agent.outsider import HELPER_JS
    for head in ("async function click(", "async function fill(", "async function check(", "async function choose(",
                 "async function hover(", "async function see(", "async function seeOnce(", "async function found("):
        body = HELPER_JS.split(head, 1)[1].split("\n}\n", 1)[0]
        calls = re.findall(r"await fail\(page, .*?\);(?=\n|$)", body, re.S)

        assert calls and all(c.rstrip().endswith("within);") for c in calls if "does not accept typing" not in c), (head, calls)

@case
def the_running_application_never_restores_seed_data():
    from agent.conventions import PROJECT_CONVENTIONS
    flat = _flat(PROJECT_CONVENTIONS)
    rule = "never restores, resets or re-seeds data"
    assert flat.count(rule) == 1, flat.count(rule)
    around = flat[flat.index("seed it idempotently"):][:700]
    assert rule in around, "merged into the seed sentence, not a second rule elsewhere"
    for clause in ("written once, at start-up, into an empty database", "after a password change the old password fails",
                   "another browser session sees the same data"):
        assert clause in around, clause
    assert all(ord(ch) < 128 for ch in around), "conventions are English only"

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
    print(f"{len(CASES) - failed}/{len(CASES)} helpers141 cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
