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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cases_visual142 import _block, _playwright_modules, decide, page, popup, selected

CASES: list = []
SKIPPED: list[str] = []

def case(function):
    CASES.append(function)
    return function

def not_reported(found, lines, words):
    assert not found, found
    assert any("diagnostic, not reported" in l and words in l for l in lines), lines

@case
def r46_raw_number_never_reported():
    snap = [page("/data", texts=[{"text": "1.2345678900000001", "tag": "td", "mono": False, "context": "Reading 1.2345678900000001"},
                                 {"text": "0.30000000000000004", "tag": "td", "mono": False, "context": "Sum 0.30000000000000004"}])]
    for requirement in ("", "Uploaded readings are displayed exactly as uploaded, with all their decimal digits."):
        found, lines = decide(snap, requirement)
        not_reported(found, lines, "do not prove it was computed")
        assert sum("not reported" in l for l in lines) == 2, lines

    found, _ = decide([page("/", texts=[{"text": "[object Object]", "tag": "span", "mono": False, "context": "Owner [object Object]"}])])
    assert len(found) == 1 and "[object Object]" in found[0]["problem"], found

@case
def r46_popup_needs_a_declared_opener():

    for pop in (popup(via="nearby", gap=0, haspopup="menu"), popup(via="nearby", gap=0, haspopup="listbox"),
                popup(via="nearby", expanded=None, haspopup="true", gap=2)):
        found, lines = decide([page("/w", popups=[pop])])
        not_reported(found, lines, "only a nearby control")

    found, _ = decide([page("/w", popups=[popup(via="aria-controls")])])
    assert len(found) == 1 and "open as soon as the page loads" in found[0]["problem"], found

@case
def r46_selection_with_uncompared_graphics():
    item = selected()
    item["opaque"] = True
    found, lines = decide([page("/v", selected=[item])])
    not_reported(found, lines, "canvas / video / frame")
    found, _ = decide([page("/v", selected=[selected()])])
    assert len(found) == 1
    item = selected()
    item["opaque"] = False
    assert len(decide([page("/v", selected=[item])])[0]) == 1

@case
def r46_blank_only_when_nothing_is_drawn():
    for drawn in ('a "Open gallery": a visible control', "div: a CSS background / mask image", "div: text of a ::before / ::after"):
        found, lines = decide([page("/g/", blank=False, drawn=drawn)])
        not_reported(found, lines, "no text, but")
    found, _ = decide([page("/g/", blank=True, drawn="")])
    assert len(found) == 1 and "renders nothing at all" in found[0]["problem"], found

def heading(**over):
    body = 'rgb(31, 35, 40)|-apple-system, "Segoe UI", sans-serif|normal|none|normal|none'
    h = {"text": "Acme docs", "size": 16, "weight": 400, "body": 16, "type": body, "backdrop": "rgb(255, 255, 255)", "edge": False,
         "ref": {"tag": "p", "size": 16, "weight": 400, "type": body, "backdrop": "rgb(255, 255, 255)"}}
    h.update(over)
    return h

@case
def r46_h1_needs_every_cue_alike():
    found, _ = decide([page("/r", heading=heading())])
    assert len(found) == 1 and "same colour, font and background" in found[0]["problem"], found
    white = 'rgb(255, 255, 255)|-apple-system, "Segoe UI", sans-serif|normal|none|normal|none'
    for h, words in ((heading(type=white, backdrop="rgb(31, 35, 40)"), "differs from the body text"),
                     (heading(backdrop="rgb(31, 35, 40)"), "another background"),
                     (heading(type=white), "differs from the body text"),
                     (heading(type=heading()["type"].replace("none|normal|none", "uppercase|normal|none")), "differs"),
                     (heading(edge=True), "own border / shadow")):
        found, lines = decide([page("/r", heading=h)])
        not_reported(found, lines, words)

    old = {"text": "Acme docs", "size": 16, "weight": 400, "body": 16}
    found, lines = decide([page("/r", heading=old)])
    not_reported(found, lines, "were not measured")

    found, _ = decide([page("/r", heading=heading(size=16, body=16, ref={**heading()["ref"], "size": 12}))])
    assert not found, found
    found, _ = decide([page("/r", heading=heading(size=16, body=20, ref={**heading()["ref"], "size": 14}))])
    assert len(found) == 1, found

BG = ("url(&quot;data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10'%3E%3Crect width='10' "
      "height='10' fill='red'/%3E%3C/svg%3E&quot;)")
STAR = ("<svg width='16' height='16' viewBox='0 0 16 16' aria-hidden='true'><path d='M8 1l2 5h5l-4 3 2 6-5-4-5 4 2-6-4-3h5z'/>"
        "</svg>")
PIX = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='8' height='8'%3E%3Crect width='8' height='8' fill='{}'/%3E%3C/svg%3E"
FIXTURES = {
    "raw_number": "<table><tr><th>Reading</th></tr><tr><td>1.2345678900000001</td></tr></table>",
    "popup_adjacent": ("<div><button aria-haspopup='menu' aria-expanded='false'>Actions</button>"
                       "<ul role='listbox' aria-label='Workspaces'><li role='option' aria-selected='true' style='font-weight:700'>Alpha"
                       "</li><li role='option'>Beta</li><li role='option'>Gamma</li></ul></div><p>Workspace content</p>"),
    "svg_fill": ("<style>button{all:unset;padding:4px}button svg{fill:#bbb}button[aria-pressed=true] svg{fill:#06c}</style>"
                 f"<div role='toolbar' aria-label='View'><button aria-pressed='true' aria-label='Grid view'>{STAR} Grid</button>"
                 f"<button aria-pressed='false' aria-label='List view'>{STAR} List</button></div>"),
    "svg_shape": ("<style>button{all:unset;padding:4px}</style><div role='toolbar' aria-label='Rating'>"
                  "<button aria-pressed='true' aria-label='Starred'><svg width='16' height='16'><use href='#full'/></svg> Star</button>"
                  "<button aria-pressed='false' aria-label='Not starred'><svg width='16' height='16'><use href='#empty'/></svg> Star"
                  "</button></div><svg style='display:none'><symbol id='full'><rect width='16' height='16'/></symbol>"
                  "<symbol id='empty'><rect width='16' height='16' fill='none' stroke='black'/></symbol></svg>"),
    "img_src": ("<div role='tablist'><div role='tab' aria-selected='true'><img alt='' width='8' height='8' src=\""
                + PIX.format("blue") + "\"> One</div><div role='tab' aria-selected='false'><img alt='' width='8' height='8' src=\""
                + PIX.format("gray") + "\"> Two</div></div>"),
    "canvas": ("<div role='tablist'><div role='tab' aria-selected='true'><canvas width='12' height='12'></canvas> One</div>"
               "<div role='tab' aria-selected='false'><canvas width='12' height='12'></canvas> Two</div></div>"),
    "tabs_alike": ("<div role=tablist aria-label='Worksheets'><div role=tab aria-selected='true'>Sheet1</div>"
                   "<div role=tab aria-selected='false'>Sheet2</div></div>"),
    "blank_bg_link": (f"<a href='/gallery' aria-label='Open gallery' style='display:block;width:900px;height:600px;"
                      f"background-image:{BG};background-size:cover'></a>"),
    "blank_bg_div": f"<div style='width:900px;height:600px;background-image:{BG}'></div>",
    "blank_role_button": "<div role='button' tabindex='0' aria-label='Start' style='width:200px;height:80px'></div>",
    "blank_pseudo_text": "<style>#r::before{content:'Loading workbook'}</style><div id='r'></div>",
    "blank_real": "<div id='root'></div>",
    "blank_shell": "<div id='root' style='min-height:600px;background:#f5f5f5'></div>",
    "blank_body_bg": f"<style>body{{background-image:{BG.replace('&quot;', chr(34))}}}</style><div id='root'></div>",
    "body_bg_h1": f"<style>body{{background-image:{BG.replace('&quot;', chr(34))}}}</style><h1>Title</h1>",
    "h1_bar": ("<header style='background:#1f2328;color:#fff;padding:24px 16px'><h1 style='font-size:16px;font-weight:400;margin:0'>"
               "Acme docs</h1></header><main style='padding:16px'><p>Paragraph text.</p></main>"),
    "h1_colour": "<h1 style='font-size:16px;font-weight:400;margin:0;color:#0969da'>Acme docs</h1><p>Paragraph text.</p>",
    "h1_plain": "<style>h1{font-size:16px;font-weight:400;margin:0}</style><h1>alice-dev/acme-docs</h1><p>text</p>",
}
_BROWSER: dict | None = None

def browser() -> dict | None:
    global _BROWSER
    if _BROWSER is not None:
        return _BROWSER or None
    modules, node = _playwright_modules(), shutil.which("node")
    if not modules or not node:
        SKIPPED.append("SKIP  r46v browser fixtures: no @playwright/test found (set ARC_PLAYWRIGHT_NODE_MODULES)")
        print(SKIPPED[-1])
        _BROWSER = {}
        return None
    probe = "const { chromium } = require('@playwright/test');\n" + _block("const VISUAL_CHECK = ", "// like GRID_TWICE: a text") + """
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
    assert "VISUAL_JSON" in done.stdout, (done.stdout[-800:], done.stderr[-800:])
    _BROWSER = json.loads(done.stdout.split("VISUAL_JSON", 1)[1].strip())
    return _BROWSER

def live(name: str, requirement: str = ""):
    return decide([{"route": "/x/", "visual": browser()[name]}], requirement)

@case
def r46_browser_raw_and_popup():
    if browser() is None:
        raise unittest.SkipTest("no @playwright/test found (set ARC_PLAYWRIGHT_NODE_MODULES)")
    assert [t["text"] for t in browser()["raw_number"]["texts"]] == ["1.2345678900000001"]
    not_reported(*live("raw_number", "Uploaded readings are displayed exactly as uploaded."), "do not prove")
    pops = browser()["popup_adjacent"]["popups"]
    assert len(pops) == 1 and pops[0]["trigger"]["via"] == "nearby" and pops[0]["trigger"]["haspopup"] == "menu", pops
    not_reported(*live("popup_adjacent"), "only a nearby control")

@case
def r46_browser_selection_graphics():
    if browser() is None:
        raise unittest.SkipTest("no @playwright/test found (set ARC_PLAYWRIGHT_NODE_MODULES)")
    r = browser()
    for name in ("svg_fill", "svg_shape", "img_src"):
        assert r[name]["selected"] == [] and r[name]["checkedSelected"] == 1, (name, r[name]["selected"])
        assert not live(name)[0], name
    assert r["canvas"]["selected"] and r["canvas"]["selected"][0]["opaque"] is True, r["canvas"]["selected"]
    not_reported(*live("canvas"), "canvas / video / frame")
    alike = r["tabs_alike"]["selected"]
    assert len(alike) == 1 and alike[0]["opaque"] is False, alike
    assert len(live("tabs_alike")[0]) == 1

@case
def r46_browser_blank():
    if browser() is None:
        raise unittest.SkipTest("no @playwright/test found (set ARC_PLAYWRIGHT_NODE_MODULES)")
    r = browser()
    expected = {"blank_bg_link": "a visible control", "blank_bg_div": "background / mask image",
                "blank_role_button": "a visible control", "blank_pseudo_text": "::before / ::after"}
    for name, why in expected.items():
        assert r[name]["blank"] is False and why in r[name]["drawn"], (name, r[name]["blank"], r[name]["drawn"])
        not_reported(*live(name), "no text, but")
    assert "Open gallery" in r["blank_bg_link"]["drawn"]

    assert r["body_bg_h1"]["heading"]["backdrop"].startswith("image url("), r["body_bg_h1"]["heading"]
    for name in ("blank_real", "blank_shell", "blank_body_bg"):
        assert r[name]["blank"] is True and r[name]["drawn"] == "", (name, r[name])
        assert len(live(name)[0]) == 1, name

@case
def r46_browser_heading():
    if browser() is None:
        raise unittest.SkipTest("no @playwright/test found (set ARC_PLAYWRIGHT_NODE_MODULES)")
    r = browser()
    assert r["h1_bar"]["heading"]["backdrop"] == "rgb(31, 35, 40)" and r["h1_bar"]["heading"]["ref"]["backdrop"] == "none"
    not_reported(*live("h1_bar"), "differs from the body text")
    not_reported(*live("h1_colour"), "differs from the body text")
    h = r["h1_plain"]["heading"]
    assert h["type"] == h["ref"]["type"] and h["backdrop"] == h["ref"]["backdrop"] == "none" and h["ref"]["tag"] == "p", h
    assert len(live("h1_plain")[0]) == 1

@case
def r46_snapshot_script():
    block = _block("const VISUAL_CHECK = ", "(async () => {")
    assert chr(92) not in block and all(ord(c) < 128 for c in block), "visual block: no backslash, ASCII only"
    for needed in ("'fill', 'stroke'", "drawing(d)", "opaque", "let drawn = ''", "!graphics && !drawn", "ref: { tag"):
        assert needed in block, needed

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
