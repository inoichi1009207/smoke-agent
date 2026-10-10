from __future__ import annotations

import inspect
import json
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

CASES = []
NL = chr(10)

def case(function):
    CASES.append(function)
    return function

def _findings(batch_text, whole, snapshot):
    from agent import detaudit
    node = {"id": "REQ-9-9", "name": "REQ-9-9", "description": batch_text, "scenarios": [], "requirement_text": whole}
    with tempfile.TemporaryDirectory() as tmp:
        return detaudit.deterministic_findings(Path(tmp), node, snapshot, log=lambda m: None)

GRID_ARIA = NL.join(['- grid "Worksheet":', '  - row "1":', '    - columnheader "A"', '    - gridcell "B2"'])

SHEET_REQ = 'Grid cells use the ARIA gridcell role with their cell coordinates as accessible names. Cell B2 shows "Data".'

@case
def a_grid_cell_named_by_its_coordinate_is_not_relabeled():
    cell = {"tag": "div", "label": "B2", "text": "Data"}
    routed = [{"route": "/workbooks/1", "url": "http://h/workbooks/1", "aria": GRID_ARIA, "relabeled": [cell]}]
    crawled_div = [{"route": "/workbooks/1", "crawled": True, "console": [], "relabeled": [cell]}]
    crawled_td = [{"route": "/workbooks/1", "crawled": True, "console": [], "relabeled": [dict(cell, tag="td")]}]
    for snapshot in (routed, crawled_div, crawled_td):
        hits = [f for f in _findings("Edit a cell.", SHEET_REQ, snapshot) if "aria-label" in f["problem"]]
        assert hits == [], hits

@case
def a_relabeled_control_is_still_reported():
    req = "The selector is a unique button named “Branch <current branch name>”."
    item = {"tag": "div", "label": "main", "text": "Branch main"}

    routed = [{"route": "/r", "url": "http://h/r", "aria": '- button "main"', "relabeled": [item]}]

    crawled = [{"route": "/r", "crawled": True, "console": [], "relabeled": [dict(item, tag="button")]}]
    for snapshot in (routed, crawled):
        hits = [f for f in _findings("Switch branches.", req, snapshot) if "aria-label" in f["problem"]]
        assert len(hits) == 1 and 'aria-label="main"' in hits[0]["problem"], hits

    from agent.detaudit import relabeled_control
    assert relabeled_control({"tag": "button", "label": "B2"}, GRID_ARIA) is False
    assert relabeled_control({"tag": "div", "label": "B2"}, GRID_ARIA + NL + '- button "B2"') is False
    assert relabeled_control({"tag": "div", "label": "Cart"}, '- region "Cart"') is False
    assert relabeled_control({"tag": "div", "label": "Cart"}, "") is False

GITHUB_LIST = NL.join([
    "- list:",
    "  - listitem:",
    '    - link "Fix search":',
    "      - /url: /acme-demo/acme-docs/pull/2",
    "  - listitem:",
    '    - link "Search release notes":',
    "      - /url: /acme-demo/acme-docs/issues/3",
])

GITHUB_REQ6 = ("PR titles are headings whose accessible name is exactly the persisted title; list titles are links with "
               "that same exact name.")
DASHBOARD = "The dashboard lists the pull requests and issues assigned to the signed-in user."

def _item_links(batch_text, whole, aria):
    snapshot = [{"route": "/", "url": "http://h/", "aria": aria}]
    return [f for f in _findings(batch_text, whole, snapshot) if "through a LINK" in f["problem"]]

@case
def a_list_the_whole_requirement_calls_links_is_not_turned_into_buttons():

    assert _item_links(DASHBOARD, DASHBOARD + NL + GITHUB_REQ6, GITHUB_LIST) == []

    assert _item_links(DASHBOARD, DASHBOARD + NL + "The “Fix search” link opens it.", GITHUB_LIST) == []

    shelves = GITHUB_LIST.replace("/acme-demo/acme-docs/pull/2", "/shelves/2").replace("/acme-demo/acme-docs/issues/3", "/shelves/3")
    assert _item_links("Browse shelves.", "Browse shelves." + NL + "Each shelf is a link to its page.", shelves) == []

@case
def the_check_still_speaks_where_nothing_calls_the_list_links():
    assert len(_item_links(DASHBOARD, DASHBOARD, GITHUB_LIST)) == 1

    shelves = GITHUB_LIST.replace("/acme-demo/acme-docs/pull/2", "/shelves/2").replace("/acme-demo/acme-docs/issues/3", "/shelves/3")
    for nav in ("The header has the “Shelves” link.", "The header has the “shelves” link.", "Click the Shelves link in the header."):
        assert len(_item_links("Browse shelves.", "Browse shelves." + NL + nav, shelves)) == 1, nav

    open_kind = "Each pull request title is a link or button that opens it."
    assert len(_item_links(DASHBOARD, DASHBOARD + NL + open_kind, GITHUB_LIST)) == 1

    assert len(_item_links(DASHBOARD, DASHBOARD + NL + "List titles are not links; a link opens the settings.", GITHUB_LIST)) == 1

@case
def the_bare_link_fix_keeps_links_the_whole_requirement_states():
    from agent import detaudit
    bare = GITHUB_LIST
    found = [f for f in _findings(DASHBOARD, DASHBOARD + NL + GITHUB_REQ6, [{"route": "/", "url": "http://h/", "aria": bare}])
             if "bare link" in f["problem"]]
    assert len(found) == 1 and "keep each one a link" in found[0]["fix"], found
    sentences = detaudit.link_sentences(DASHBOARD + NL + GITHUB_REQ6 + " Version 6.1.1 is out.")

    assert sentences == ["list titles are links with that same exact name."], sentences

ENGLISH_UI = ("The header shows `登录` or `Login`. Buttons: `Save`, `Cancel`, `Delete`, `Edit`, `Search`, `Settings`, "
              "`Profile`, `Sign out`, `Books`, `Shelves`, `Help`.")

@case
def the_alternative_in_the_interface_language_is_kept():
    from agent.contract import dedupe_alternatives
    from agent.detaudit import alternative_pairs, interface_alternative
    assert alternative_pairs(ENGLISH_UI) == [("登录", "Login")]
    assert interface_alternative("登录", "Login", ENGLISH_UI) == ("Login", "登录")
    assert interface_alternative("登录", "Login", "首页提供 `登录` 或 `Login` 入口") == ("登录", "Login")

    chinese_prose = ("在页面顶部的导航栏右侧显示一个用于进入账户的入口，用户点击之后进入对应的页面并完成后续的操作流程。" * 3
                     + "入口为 `登录` 或 `Login`；按钮有 `Save`、`Cancel`、`Delete`、`Edit`、`Search`、`Settings`、`Profile`、"
                     "`Help`、`Books`、`Shelves`。")
    assert interface_alternative("登录", "Login", chinese_prose) == ("Login", "登录")
    text = json.dumps({"pages": [{"route": "/", "links": [{"text": "登录", "href": "/login"}, {"text": "Login", "href": "/login"}]}]},
                      ensure_ascii=False)
    out = json.loads(dedupe_alternatives(text, ENGLISH_UI, log=lambda m: None))
    assert [link["text"] for link in out["pages"][0]["links"]] == ["Login"], out
    snapshot = [{"route": "/", "url": "http://h/", "aria": '- link "登录":' + NL + '  - /url: /login' + NL + '- link "Login":' + NL + '  - /url: /login'}]
    hits = [f for f in _findings(ENGLISH_UI, ENGLISH_UI, snapshot) if "ALTERNATIVE" in f["problem"]]
    assert len(hits) == 1 and "named `Login`" in hits[0]["fix"] and "remove the `登录`" in hits[0]["fix"], hits

CHIPS = NL.join(["- list:", "  - listitem:", '    - button "Work"', "  - listitem:", '    - button "Personal"'])

@case
def filter_and_tag_buttons_are_not_item_titles():
    from agent.detaudit import bare_item_titles
    assert bare_item_titles(CHIPS) == ["Work", "Personal"]
    pressed = CHIPS.replace('button "Work"', 'button "Work" [pressed]')
    assert bare_item_titles(pressed) == []
    assert bare_item_titles(CHIPS, 'Seed data: label "Work" and label "Personal".') == []
    assert bare_item_titles(CHIPS, "Filter the notes by the `Work` tag or the `Personal` tag.") == []
    sorted_ = CHIPS.replace('"Work"', '"Filter by date"').replace('"Personal"', '"Sort by title"')
    assert bare_item_titles(sorted_) == []

    page = [{"route": "/", "url": "http://h/", "aria": CHIPS}]
    items = lambda whole: [f for f in _findings("Show the notes.", whole, page) if "bare link" in f["problem"]]
    assert len(items("Show the notes.")) == 1
    assert items('Show the notes. Seed data: label "Work" and label "Personal".') == []

@case
def record_titles_as_bare_buttons_are_still_reported():
    from agent.detaudit import bare_item_titles
    pages = CHIPS.replace('"Work"', '"Page Created 6.1.1"').replace('"Personal"', '"Page 2"')
    requirement = 'The book contains the page "Page Created 6.1.1"; a field labeled "Title".'
    assert bare_item_titles(pages, requirement) == ["Page Created 6.1.1", "Page 2"]

    both = pages + NL + CHIPS.replace('button "Work"', 'button "Work" [pressed]')
    assert bare_item_titles(both, requirement) == ["Page Created 6.1.1", "Page 2"]

@case
def statedrole_k7_is_wired_with_the_add_fix():
    import cases_statedrole as sr
    from agent import detaudit
    node = {"id": "REQ-6-2-2", "name": "REQ-6-2-2", "description": sr.REQ_6_2_2, "scenarios": [], "requirement_text": sr.REQ}
    with tempfile.TemporaryDirectory() as tmp:
        root = sr._project(tmp, {"frontend/src/pages/ComparePage.tsx": sr.COMPARE_H1})
        findings, counts = detaudit.stated_control_findings(node, root)
    mine = [f for f in findings if "Compare changes" in f["problem"]]
    assert len(mine) == 1 and "Keep that heading" in mine[0]["fix"] and "ADD" in mine[0]["fix"], findings
    assert "in place of the element" not in mine[0]["fix"] and counts["stated_role"] == 1

@case
def statedrole_k3_breadcrumbs_are_wired():
    from agent import detaudit
    assert "findings.extend(breadcrumb_findings(whole, project_dir)[:MAX_FINDINGS_PER_CHECK])" in inspect.getsource(
        detaudit.deterministic_findings)
    original = detaudit.breadcrumb_findings
    seen = []

    def fake(requirement_text, project_dir):
        seen.append(requirement_text)
        return [{"where": f"frontend/src/pages/FilePage.tsx:{i}", "requirement": "(breadcrumb)", "problem": "crumb", "fix": "x"}
                for i in range(20)]

    detaudit.breadcrumb_findings = fake
    try:
        found = _findings("Browse files.", "Browse files. The breadcrumbs can return to the parent directory.", [])
    finally:
        detaudit.breadcrumb_findings = original
    crumbs = [f for f in found if f["problem"] == "crumb"]
    assert len(crumbs) == detaudit.MAX_FINDINGS_PER_CHECK and seen and "breadcrumbs can return" in seen[0], (len(crumbs), seen)

def main() -> int:
    failed = 0
    for function in CASES:
        try:
            function()
        except Exception:
            failed += 1
            print(f"FAIL {function.__name__}")
            traceback.print_exc()
    print(f"{len(CASES) - failed}/{len(CASES)} consist1 cases passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
