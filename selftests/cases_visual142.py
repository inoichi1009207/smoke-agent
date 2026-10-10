from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES: list = []
SKIPPED: list[str] = []

def case(function):
    CASES.append(function)
    return function

def decide(snapshot, requirement=""):
    from agent.visual_checks import visual_findings
    lines: list[str] = []
    found = visual_findings(requirement, snapshot, log=lines.append)
    return found, lines

def page(route="/", crawled=False, **visual):
    base = {"texts": [], "popups": [], "selected": [], "checkedSelected": 0, "blank": False, "heading": None, "fields": []}
    base.update(visual)
    entry = {"route": route, "visual": base}
    if crawled:
        entry["crawled"] = True
    return entry

def text(value, context=None, mono=False):
    return {"text": value, "tag": "span", "mono": mono, "context": context or value}

def popup(expanded="false", via="aria-controls", shown=True, focused=False, haspopup="listbox", controls="", gap=4,
          on_top=9, overlay=True, covers=("Region",)):
    return {"role": "listbox", "label": "Branches", "items": 4, "first": ["a", "b", "c"], "rect": [10, 40, 200, 120],
            "position": "absolute", "overlay": overlay, "points": 9, "onTop": on_top, "covers": list(covers), "focused": focused,
            "trigger": {"name": "Branch main", "tag": "button", "role": "", "via": via, "expanded": expanded,
                        "haspopup": haspopup, "controls": controls, "shown": shown, "gap": gap}}

def selected(text_="Sheet1", attr="aria-selected", group="Worksheets", mark=False, indicator=False, siblings=1):
    return {"attr": attr, "value": "true", "role": "tab", "tag": "div", "text": text_, "group": group, "siblings": siblings,
            "siblingTexts": ["Sheet2"], "mark": mark, "indicator": indicator}

def has(found, word):
    return [f for f in found if word in f["problem"]]

@case
def iso_timestamp_reported_once_for_all_pages():
    snap = [page("/", texts=[text("2026-09-29T05:33:59.795Z", "Q3 Sales Last updated: 2026-09-29T05:33:59.795Z")]),
            page("/workbooks/1", texts=[text("Last updated: 2026-09-29T05:33:59.795Z")])]
    found, _ = decide(snap)
    iso = has(found, "Raw machine timestamps")
    assert len(iso) == 1, found
    assert "route /, /workbooks/1" == iso[0]["where"], iso[0]["where"]
    assert "Last updated: 2026-09-29T05:33:59.795Z" in iso[0]["problem"]
    assert "HH:mm:ss" in iso[0]["fix"] and "requirement states another display format" in iso[0]["fix"]
    assert set(iso[0]) >= {"where", "requirement", "problem", "fix"}

@case
def iso_not_reported_when_monospace_or_stated():
    snap = [page("/", texts=[text("2026-09-29T05:33:59Z", mono=True)])]
    found, lines = decide(snap)
    assert not found and any("monospace" in l for l in lines), (found, lines)
    snap = [page("/", texts=[text("Updated 2026-09-29T05:33:59Z")])]
    for requirement in ("Times are shown in ISO 8601.", "shown as UTC", "e.g. 2024-01-02T10:00 in the list", "toISOString()"):
        found, lines = decide(snap, requirement)
        assert not found and any("diagnostic, not reported" in l for l in lines), requirement

    found, _ = decide(snap, "the requested workflow ISO the requested workflow")
    assert len(has(found, "Raw machine timestamps")) == 1

@case
def iso_shapes():
    from agent.visual_checks import ISO
    for good in ("2026-09-29T05:33:59.795Z", "2026-09-29T05:33", "2026-09-29T05:33:59+02:00"):
        assert ISO.search(good), good
    for bad in ("2026-09-29", "2026-09-29 05:33", "id-2026-09-29T05:33x", "12026-09-29T05:33:59Z9"):
        assert not ISO.search(bad), bad

@case
def float_noise_logged_only():

    snap = [page("/w", texts=[text("0.30000000000000004"), text("0.7999999999999999")])]
    for requirement in ("", "the cell shows 0.30000000000000004 and 0.7999999999999999"):
        found, lines = decide(snap, requirement)
        assert not found, found
        assert sum("looks like binary float noise" in l and "not reported" in l for l in lines) == 2, lines

    for value in ("5.333333333333333", "0.3333333333333333", "3.1000000", "1.0000001", "v1.000000.2", "1000000.5"):
        found, lines = decide([page("/w", texts=[text(value)])])
        assert not found and not any("binary float noise" in l for l in lines), value

@case
def leaked_values():
    for shown, token in (("[object Object]", "[object Object]"), ("Due: Invalid Date", "Invalid Date"), ("NaN", "NaN"),
                         ("Total: NaN%", "NaN"), ("undefined", "undefined"), ("Owner: undefined", "undefined")):
        found, _ = decide([page("/", texts=[text(shown)])])
        assert len(has(found, f"shown as the text “{token}”")) == 1, shown
    for shown in ("undefined behaviour is avoided", "Banana", "NaNa", "the value is undefined here"):
        found, _ = decide([page("/", texts=[text(shown)])])
        assert not found, (shown, found)
    found, lines = decide([page("/", texts=[text("NaN")])], "an empty cell shows NaN")
    assert not found and any("requirement uses it" in l for l in lines)

@case
def popup_open_while_opener_says_closed():
    found, _ = decide([page("/r", popups=[popup()])])
    assert len(found) == 1 and "open as soon as the page loads" in found[0]["problem"], found
    assert 'aria-expanded="false"' in found[0]["problem"] and "hides “Region”" in found[0]["problem"]
    assert "Render it closed on load" in found[0]["fix"] and "Only if the requirement states" in found[0]["fix"]

    found, _ = decide([page("/r", popups=[popup(expanded=None, overlay=False, covers=())])])
    assert len(found) == 1 and "aria-haspopup" in found[0]["problem"] and "pushes the content" in found[0]["problem"]

@case
def popup_not_reported_when_unsure():
    unsure = [
        popup(expanded="true"),
        popup(shown=False),
        popup(focused=True),
        popup(via="nearby", gap=120),
        popup(via="nearby", controls="other-menu"),
        popup(expanded=None, haspopup=""),
        popup(on_top=0),
    ]
    for pop in unsure:
        found, lines = decide([page("/r", popups=[pop])])
        assert not found and any("diagnostic, not reported" in l for l in lines), pop
    no_opener = popup()
    no_opener["trigger"] = None
    found, lines = decide([page("/r", popups=[no_opener])])
    assert not found and any("no opener declares it" in l for l in lines)

    found, _ = decide([page(f"/r{i}", popups=[popup(), popup()]) for i in range(4)])
    assert len(found) == 2, found

@case
def unmarked_selection():
    found, _ = decide([page("/w/1", selected=[selected()])])
    assert len(found) == 1 and "looks exactly like its 1 unmarked sibling" in found[0]["problem"], found
    assert '[aria-selected="true"]' in found[0]["fix"] and "Keep the attribute" in found[0]["fix"]
    for item in (selected(mark=True), selected(indicator=True), selected(siblings=0), selected(text_=" ")):
        found, lines = decide([page("/w/1", selected=[item])])
        assert not found and any("diagnostic, not reported" in l for l in lines), item

    snap = [page(f"/repo?branch={b}", crawled=True, selected=[selected(text_=b, group="Branches")]) for b in "abcd"]
    snap.append(page("/repo/pulls", selected=[selected(text_="Open", attr="aria-current", group="Filters")]))
    found, _ = decide(snap)
    assert len(found) == 2, found
    assert found[0]["where"] == "route /repo?branch=a"

@case
def blank_pages():
    found, _ = decide([page("/x/", blank=True)])
    assert len(found) == 1 and "renders nothing at all" in found[0]["problem"]
    found, lines = decide([page("/x/", crawled=True, blank=True)])
    assert not found and any("left to the console check" in l for l in lines)
    entry = page("/x/", blank=True)
    entry["error"] = "Timeout"
    found, _ = decide([entry])
    assert not found

@case
def unstyled_h1():
    look = "rgb(0, 0, 0)|sans-serif|normal|none|normal|none"
    ref = {"tag": "p", "size": 16, "weight": 400, "type": look, "backdrop": "none"}
    head = lambda size, weight, body=16: {"text": "alice-dev/acme-docs", "size": size, "weight": weight, "body": body,
                                          "type": look, "backdrop": "none", "edge": False, "ref": {**ref, "size": body}}
    found, _ = decide([page(f"/p{i}", heading=head(16, 400)) for i in range(5)])
    assert len(found) == 1 and "set like body text on 5 page(s)" in found[0]["problem"], found
    assert "16px at weight 400" in found[0]["problem"] and "keep its text" in found[0]["fix"]
    for h in (head(20, 600), head(16, 700), head(18.5, 400), head(24, 400), None, {"text": "", "size": 16, "weight": 400, "body": 16}):
        found, _ = decide([page("/", heading=h)])
        assert not found, h

    found, lines = decide([page("/", heading={"text": "alice-dev/acme-docs", "size": 16, "weight": 400, "body": 16})])
    assert not found and any("were not measured" in l for l in lines), lines

@case
def robust_against_junk():
    for snap in (None, [], [None, 3, "x"], [{"route": "/"}], [{"route": "/", "visual": None}]):
        found, _ = decide(snap)
        assert found == [], snap
    found, lines = decide([page("/", texts=5, popups=[None, 1], selected="x", heading=[1])])
    assert found == [] and any("crashed" in l for l in lines), lines

    found, lines = decide([page("/", fields=[{"name": "Find a repository", "tag": "input", "rect": [0, 0, 200, 20]}])])
    assert not found and any("no border, shadow" in l for l in lines)

@case
def live_measurements():
    sheet = [
        page("/", texts=[{"text": "2026-09-29T10:34:12.124Z", "tag": "span", "mono": False,
                          "context": "Q3 SalesLast updated: 2026-09-29T10:34:12.124Z"}],
             heading={"text": "Workbooks", "size": 20, "weight": 600, "body": 16}),
        page("/workbooks/new", heading={"text": "New blank workbook", "size": 20, "weight": 600, "body": 16}),
        page("/workbooks/1", checkedSelected=2, heading={"text": "Q3 Sales", "size": 20, "weight": 600, "body": 16},
             texts=[{"text": "2026-09-29T10:34:12.124Z", "tag": "p", "mono": False,
                     "context": "Q3 Sales Rename workbook Last updated: 2026-09-29T10:34:12.124Z Sheet1 ▾ Sheet2 ▾ Add worksheet"}],
             selected=[{"attr": "aria-selected", "value": "true", "role": "tab", "tag": "div", "text": "Sheet1", "group": "Worksheets",
                        "siblings": 1, "siblingTexts": ["Sheet2"], "mark": False, "indicator": False}]),
        page("/workbooks/", blank=True),
    ]
    found, _ = decide(sheet, "Each record displays \"Last updated: <last updated value>\" ... workflow ISO the requested workflow")
    kinds = sorted(f["requirement"] for f in found)
    assert kinds == ["(dates and times are shown readable for a person)", "(every page shows something)",
                     "(the selected / current item is visibly marked)"], kinds
    branch = lambda b: {"attr": "aria-selected", "value": "true", "role": "option", "tag": "button", "text": b,
                        "group": "Branches", "siblings": 3, "siblingTexts": [], "mark": False, "indicator": False}
    listbox = {"role": "listbox", "label": "Branches", "items": 4, "first": ["draft-feature", "feature-search", "main"],
               "rect": [33, 375, 238, 112], "position": "static", "overlay": False, "points": 9, "onTop": 9, "covers": [],
               "focused": False, "trigger": None}

    look = ('oklch(0.21 0.034 264.665)|ui-sans-serif, system-ui, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", '
            '"Segoe UI Symbol", "Noto Color Emoji"|normal|none|normal|none')
    h1 = {"text": "alice-dev/acme-docs", "size": 16, "weight": 400, "body": 16, "type": look, "backdrop": "rgb(255, 255, 255)",
          "edge": False, "ref": {"tag": "p", "size": 16, "weight": 400, "type": look, "backdrop": "rgb(255, 255, 255)"}}
    github = [page("/"), page("/login"),
              page("/search?q=acme", texts=[text("2026-09-29T10:35:03.207Z", "Public documentation repository Updated 2026-09-29T10:35:03.207Z")])]
    github += [page(f"/alice-dev/acme-docs{q}", crawled=bool(q), popups=[listbox], selected=[branch(b)], heading=h1)
               for q, b in (("", "main"), ("?branch=draft-feature", "draft-feature"), ("?branch=release", "release"))]
    found, lines = decide(github)
    kinds = sorted(f["requirement"] for f in found)
    assert kinds == ["(a page title looks like a title)", "(dates and times are shown readable for a person)",
                     "(the selected / current item is visibly marked)"], kinds
    assert any("no opener declares it" in l for l in lines)

def _block(start: str, end: str) -> str:
    from agent import audit
    script = audit.SNAPSHOT_SCRIPT
    i = script.index(start)
    return script[i:script.index(end, i)]

@case
def snapshot_script_wiring():
    from agent import audit
    script = audit.SNAPSHOT_SCRIPT
    visual = _block("const VISUAL_CHECK = ", "(async () => {")
    assert chr(92) not in visual, "no backslash in the visual block (non-raw Python string)"
    assert script.count("entry.visual = await VISUAL_TWICE(page);") == 3
    assert "else if (VISUAL_HIT(entry.visual)) out.push(entry);" in script
    assert "else if (entry.via && VISUAL_HIT(entry.visual)) out.push(entry);" in script

    assert script.index("entry.visual = await VISUAL_TWICE(page);") < script.index("entry.covered = await page.evaluate(")

    for kept in ("entry.rowLayout = await ROW_TWICE(page);", "entry.gridLayout = await GRID_TWICE(page);", "entry.covered",
                 "entry.titleTwice", "ARC_SNAPSHOT_JSON"):
        assert kept in script, kept

@case
def visual_twice_semantics():
    node = shutil.which("node")
    assert node, "node is needed"
    block = _block("const VISUAL_HIT = ", "(async () => {")
    probe = "const VISUAL_CHECK = null;" + chr(10) + block + """
const looks = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));
(async () => {
  const out = [];
  for (const pair of looks) {
    let i = 0;
    let waited = 0;
    const page = { evaluate: async () => pair[i++], waitForTimeout: async (ms) => { waited += ms; } };
    const r = await VISUAL_TWICE(page);
    out.push({ calls: i, waited, texts: r.texts.length, popups: r.popups.length, selected: r.selected.length, blank: r.blank });
  }
  console.log(JSON.stringify(out));
})();
"""
    empty = {"ms": 1, "texts": [], "popups": [], "selected": [], "blank": False}
    hit = {"ms": 1, "texts": [{"text": "NaN"}], "popups": [{"role": "menu", "first": ["a"]}],
           "selected": [{"attr": "aria-selected", "text": "Sheet1"}], "blank": True}
    other = {"ms": 1, "texts": [{"text": "undefined"}], "popups": [{"role": "menu", "first": ["b"]}],
             "selected": [{"attr": "aria-current", "text": "Sheet1"}], "blank": False}
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "t.cjs").write_text(probe, encoding="utf-8")
        (Path(tmp) / "d.json").write_text(json.dumps([[empty, hit], [hit, hit], [hit, other], [hit, empty]]), encoding="utf-8")
        done = subprocess.run([node, str(Path(tmp) / "t.cjs"), str(Path(tmp) / "d.json")], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-800:]
    r = json.loads(done.stdout)
    assert r[0] == {"calls": 1, "waited": 0, "texts": 0, "popups": 0, "selected": 0, "blank": False}, r[0]
    assert r[1] == {"calls": 2, "waited": 600, "texts": 1, "popups": 1, "selected": 1, "blank": True}, r[1]
    assert r[2] == {"calls": 2, "waited": 600, "texts": 0, "popups": 0, "selected": 0, "blank": False}, r[2]
    assert r[3] == {"calls": 2, "waited": 600, "texts": 0, "popups": 0, "selected": 0, "blank": False}, r[3]

FIXTURES = {
    "texts": """<p>Last updated: <span id=u>2026-09-29T05:33:59.795Z</span></p><table><tr><td>0.30000000000000004</td>
      <td>5.333333333333333</td></tr></table><code>2026-09-29T05:33:59Z</code><pre>NaN</pre><input value="2026-09-29T05:33:59Z">
      <div>[object Object]</div><div style="display:none">undefined</div><p>Banana</p>""",
    "popup_closed_state": """<div style="position:relative"><button aria-haspopup="listbox" aria-expanded="false"
      aria-controls="lb">Branch main</button><ul id=lb role=listbox style="position:absolute;top:24px;left:0;margin:0;
      background:#fff;border:1px solid #999;width:200px"><li role=option>alpha</li><li role=option>beta</li></ul></div>
      <p>Region Sales Status</p><p>East 1200 Open</p><p>North 800 Closed</p>""",
    "popup_fine": """<button aria-haspopup="menu" aria-expanded="false" aria-controls="m1">Options</button>
      <div id=m1 role=menu style="display:none"><div role=menuitem>Rename</div></div>
      <button aria-haspopup="menu" aria-expanded="true" aria-controls="m2">More</button>
      <div id=m2 role=menu><div role=menuitem>Delete</div></div>
      <h2>Items</h2><ul role=listbox aria-label="Items"><li role=option aria-selected="true" style="font-weight:700">One</li>
      <li role=option aria-selected="false">Two</li></ul>""",
    "listbox_persistent": """<h2>Mail</h2><ul role=listbox aria-label="Messages"><li role=option>Hello</li>
      <li role=option>Invoice</li></ul>""",
    "tabs_alike": """<div role=tablist aria-label="Worksheets"><div role=tab aria-selected="true">Sheet1</div>
      <div role=tab aria-selected="false">Sheet2</div><div role=tab aria-selected="false">Sheet3</div></div>""",
    "tabs_bold": """<style>[role=tab][aria-selected=true]{font-weight:700}</style><div role=tablist><div role=tab
      aria-selected="true">Sheet1</div><div role=tab aria-selected="false">Sheet2</div></div>""",
    "tabs_indicator": """<div role=tablist style="position:relative;display:flex;gap:8px"><button role=tab aria-selected="true"
      style="all:unset;width:80px">Sheet1</button><button role=tab aria-selected="false" style="all:unset;width:80px">Sheet2</button>
      <span style="position:absolute;left:0;top:22px;width:80px;height:2px;background:#06c"></span></div>""",
    "tabs_check": """<div role=tablist><div role=tab aria-selected="true">&#10003; main</div><div role=tab
      aria-selected="false">dev</div></div>""",
    "nav_alike": """<nav><ul><li><a href="/code" aria-current="page">Code</a></li><li><a href="/issues">Issues</a></li>
      <li><a href="/pulls">Pull requests</a></li></ul></nav>""",
    "nav_styled": """<style>a[aria-current=page]{border-bottom:2px solid orange}</style><nav><a href="/code"
      aria-current="page">Code</a> <a href="/issues">Issues</a></nav>""",
    "crumbs": """<nav aria-label="Breadcrumb"><ol><li><a href="/">Home</a></li><li><a href="/a" aria-current="page">A</a></li></ol></nav>""",
    "pressed_alike": """<div role=toolbar><button aria-pressed="true">Bold</button><button aria-pressed="false">Italic</button></div>""",
    "blank": "",
    "svg_only": """<svg width="100" height="100"><circle cx="50" cy="50" r="40"></circle></svg>""",
    "h1_plain": """<style>h1{font-size:16px;font-weight:400;margin:0}</style><h1>alice-dev/acme-docs</h1><p>text</p>""",
}

def _playwright_modules() -> Path | None:
    candidates = [os.environ.get("ARC_PLAYWRIGHT_NODE_MODULES", ""), str(ROOT / "template" / "backend" / "node_modules"),
                  "local-data/try/sheet-v141/template/backend/node_modules"]
    for c in candidates:
        if c and (Path(c) / "@playwright" / "test").is_dir():
            return Path(c)
    return None

_BROWSER: dict | None = None

def browser_results() -> dict | None:
    global _BROWSER
    if _BROWSER is not None:
        return _BROWSER or None
    modules = _playwright_modules()
    node = shutil.which("node")
    if not modules or not node:
        SKIPPED.append("SKIP  browser fixtures: no @playwright/test found (set ARC_PLAYWRIGHT_NODE_MODULES)")
        print(SKIPPED[-1])
        _BROWSER = {}
        return None
    block = _block("const VISUAL_CHECK = ", "// like GRID_TWICE: a text")
    probe = "const { chromium } = require('@playwright/test');\n" + block + """
const fixtures = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));
(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const out = {};
  for (const [name, html] of Object.entries(fixtures)) {
    const page = await context.newPage();
    await page.setContent('<!doctype html><html><body>' + html + '</body></html>');
    out[name] = await page.evaluate(VISUAL_CHECK);
    await page.close();
  }
  await browser.close();
  console.log('VISUAL_JSON' + JSON.stringify(out));
})().catch((e) => { console.log('VISUAL_ERR ' + e); process.exit(2); });
"""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "v.cjs").write_text(probe, encoding="utf-8")
        (Path(tmp) / "f.json").write_text(json.dumps(FIXTURES), encoding="utf-8")
        done = subprocess.run([node, str(Path(tmp) / "v.cjs"), str(Path(tmp) / "f.json")], capture_output=True, text=True,
                              timeout=180, env={**os.environ, "NODE_PATH": str(modules)}, encoding="utf-8", errors="replace")
    marker = "VISUAL_JSON"
    assert marker in done.stdout, (done.stdout[-800:], done.stderr[-800:])
    _BROWSER = json.loads(done.stdout.split(marker, 1)[1].strip())
    return _BROWSER

def _visual_page(name: str, route: str = "/") -> dict:
    results = browser_results()
    return {"route": route, "visual": results[name]}

@case
def browser_texts():
    if browser_results() is None:
        raise unittest.SkipTest("no @playwright/test found (set ARC_PLAYWRIGHT_NODE_MODULES)")
    v = browser_results()["texts"]
    shown = [t["text"] for t in v["texts"]]
    assert "2026-09-29T05:33:59.795Z" in shown and "0.30000000000000004" in shown and "[object Object]" in shown, shown

    assert not any("05:33:59Z" == s or s == "NaN" or s == "undefined" for s in shown), shown
    found, lines = decide([_visual_page("texts")])
    kinds = sorted(f["requirement"] for f in found)

    assert kinds == ["(dates and times are shown readable for a person)", "(visible values are real values)"], kinds
    assert any("0.30000000000000004" in l and "not reported" in l for l in lines), lines

@case
def browser_popups():
    if browser_results() is None:
        raise unittest.SkipTest("no @playwright/test found (set ARC_PLAYWRIGHT_NODE_MODULES)")
    pops = browser_results()["popup_closed_state"]["popups"]
    assert len(pops) == 1 and pops[0]["trigger"]["expanded"] == "false" and pops[0]["trigger"]["via"] == "aria-controls", pops
    assert pops[0]["overlay"] and pops[0]["onTop"] > 0 and any("Region" in c or "East" in c for c in pops[0]["covers"]), pops
    found, _ = decide([_visual_page("popup_closed_state")])
    assert len(found) == 1 and "open as soon as the page loads" in found[0]["problem"], found
    fine = browser_results()["popup_fine"]

    got = sorted((p["role"], p["trigger"]["via"], p["trigger"]["controls"]) for p in fine["popups"])
    assert got == [("listbox", "nearby", "m2"), ("menu", "aria-controls", "m2")], fine["popups"]
    found, _ = decide([_visual_page("popup_fine")])
    assert not found, found
    persistent = browser_results()["listbox_persistent"]["popups"]
    assert len(persistent) == 1 and persistent[0]["trigger"] is None, persistent
    assert not decide([_visual_page("listbox_persistent")])[0]

@case
def browser_selection():
    if browser_results() is None:
        raise unittest.SkipTest("no @playwright/test found (set ARC_PLAYWRIGHT_NODE_MODULES)")
    r = browser_results()
    alike = r["tabs_alike"]["selected"]
    assert len(alike) == 1 and alike[0]["text"] == "Sheet1" and alike[0]["siblings"] == 2 and not alike[0]["indicator"], alike
    assert r["tabs_bold"]["selected"] == [] and r["tabs_bold"]["checkedSelected"] == 1
    assert r["tabs_indicator"]["selected"][0]["indicator"] is True, r["tabs_indicator"]
    assert r["tabs_check"]["selected"][0]["mark"] is True, r["tabs_check"]
    assert r["nav_alike"]["selected"][0]["attr"] == "aria-current", r["nav_alike"]
    assert r["nav_styled"]["selected"] == [] and r["crumbs"]["selected"] == [] and r["crumbs"]["checkedSelected"] == 0
    assert r["pressed_alike"]["selected"][0]["attr"] == "aria-pressed"
    assert r["popup_fine"]["selected"] == []
    for name, expected in (("tabs_alike", 1), ("tabs_bold", 0), ("tabs_indicator", 0), ("tabs_check", 0), ("nav_alike", 1),
                           ("nav_styled", 0), ("crumbs", 0), ("pressed_alike", 1)):
        found, _ = decide([_visual_page(name)])
        assert len(found) == expected, (name, found)

@case
def browser_blank_and_heading():
    if browser_results() is None:
        raise unittest.SkipTest("no @playwright/test found (set ARC_PLAYWRIGHT_NODE_MODULES)")
    r = browser_results()
    assert r["blank"]["blank"] is True and r["svg_only"]["blank"] is False and r["texts"]["blank"] is False
    assert len(decide([_visual_page("blank", "/x/")])[0]) == 1 and not decide([_visual_page("svg_only")])[0]
    h = r["h1_plain"]["heading"]
    assert {k: h[k] for k in ("text", "size", "weight", "body")} == {"text": "alice-dev/acme-docs", "size": 16, "weight": 400, "body": 16}, h
    assert h["type"] == h["ref"]["type"] and h["backdrop"] == h["ref"]["backdrop"] and h["edge"] is False, h
    assert len(decide([_visual_page("h1_plain")])[0]) == 1

if __name__ == "__main__":
    failed = 0
    for function in CASES:
        try:
            function()
            print(f"ok    {function.__name__}")
        except unittest.SkipTest as why:
            print(f"SKIP  {function.__name__}: {why}")
        except Exception as exc:
            failed += 1
            print(f"FAIL  {function.__name__}: {exc!r}"[:600])
    print(f"{len(CASES) - failed}/{len(CASES)} self-tests passed" + (f"; {len(SKIPPED)} skipped" if SKIPPED else ""))
    sys.exit(1 if failed else 0)
